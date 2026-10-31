"""HTTP API（标准库实现，无框架依赖）。

生产用 FastAPI（方案 §11）；MVP 用标准库 http.server，保证在任何装了
Python 3.10+ 的机器上零依赖可跑。

安全约定：
  * actor_id 一律来自请求体/查询参数，**不接受调用方声明岗位**，
    岗位由 actor_id 前缀解析（真实系统应由独立认证服务签发）
  * 越权字段在服务端裁剪后才下发（见 authz.trim_candidates）
  * 本服务没有任何写向业务系统的端点（P1）
"""
from __future__ import annotations

import json
import os
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import audit, authz, handover, replay, sync
from .db import jload
from .metrics import process_metrics
from .service import SuggestionService, render_card

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

# 单一连接 + 多线程：所有 DB 操作串行化（SQLite 单写者模型 + 哈希链的顺序性要求）
DB_LOCK = threading.RLock()


class Handler(BaseHTTPRequestHandler):
    conn = None          # 由 serve() 注入
    server_version = "SuggestionService/0.1-mvp"

    def log_message(self, fmt, *args):   # 静音默认访问日志
        pass

    # ------------------------------------------------------------ 基础

    def _send(self, code: int, payload, ctype="application/json; charset=utf-8"):
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        elif isinstance(payload, bytes):
            body = payload
        else:
            body = str(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    # ------------------------------------------------------------ 路由

    def do_GET(self):
        with DB_LOCK:
            self._do_GET()

    def do_POST(self):
        with DB_LOCK:
            self._do_POST()

    def _do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = u.path.rstrip("/") or "/"
        try:
            if path in ("/", "/index.html"):
                return self._send(200, self._static("index.html"), "text/html; charset=utf-8")
            if path == "/api/health":
                return self._send(200, {"ok": True, "service": "建议服务 MVP"})
            if path == "/api/status":
                return self._send(200, self._status())
            if path == "/api/metrics":
                return self._send(200, process_metrics(self.conn))
            if path == "/api/suggestions":
                svc = SuggestionService(self.conn)
                limit = int(q.get("limit", 30))
                return self._send(200, {"items": svc.list_suggestions(limit)})
            if path.startswith("/api/suggestion/"):
                svc = SuggestionService(self.conn)
                sid = path.rsplit("/", 1)[-1]
                row = svc.get_suggestion(sid)
                if not row:
                    return self._send(404, {"error": "建议不存在"})
                # 行结构 → 扁平的建议内容
                p = dict(row["payload"])
                p["suggestion_id"] = row["suggestion_id"]
                p["created_at"] = row["created_at"]
                p["record_hash"] = row["record_hash"]
                p["prev_hash"] = row["prev_hash"]
                role = authz.role_of(q.get("actor_id", "CO-0001"))
                cands = (p.get("suggestion_result") or {}).get("candidates") or []
                p["suggestion_result"] = dict(p["suggestion_result"])
                p["suggestion_result"]["candidates"] = authz.trim_candidates(cands, role)
                p["card_text"] = render_card(p)
                p["_scope"] = authz.visible_materials(role)
                return self._send(200, p)
            if path.startswith("/api/audit/verify"):
                return self._send(200, audit.chain_status(self.conn))
            if path.startswith("/api/audit/"):
                biz = path.rsplit("/", 1)[-1]
                return self._send(200, audit.rebuild(self.conn, biz))
            if path.startswith("/api/handover/"):
                biz = path.rsplit("/", 1)[-1]
                if biz == "pending":
                    return self._send(200, {"items": handover.pending_escalations(self.conn)})
                return self._send(200, handover.status(self.conn, biz))
            if path == "/api/replay/latest":
                return self._send(200, replay.latest_run(self.conn) or {"metrics": {}})
            if path == "/api/divisions":
                from .anchor import load_divisions
                divs = load_divisions(self.conn)
                return self._send(200, {"items": [
                    {"code": d["code"], "name": d["name"], "level": d["level"],
                     "aliases": d["aliases"], "lat": d["lat"], "lng": d["lng"]}
                    for d in divs]})
            if path == "/api/assets":
                rows = self.conn.execute(
                    "SELECT asset_pk, asset_code, asset_category, alias_text, lng, lat,"
                    " coord_sys, location_anchor, ambiguity_flag, stale_flag"
                    " FROM asset_snapshot ORDER BY asset_pk LIMIT ?",
                    (int(q.get("limit", 50)),)).fetchall()
                items = []
                for r in rows:
                    d = dict(r)
                    d["alias_text"] = jload(d["alias_text"], [])
                    d["ambiguity_flag"] = bool(d["ambiguity_flag"])
                    d["stale_flag"] = bool(d["stale_flag"])
                    items.append(d)
                return self._send(200, {"items": items})
            return self._send(404, {"error": "未知路径", "path": path})
        except authz.AuthorizationError as e:
            return self._send(403, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": str(e), "trace": traceback.format_exc(limit=3)})

    def _do_POST(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        body = self._body()
        try:
            if path == "/api/suggest":
                svc = SuggestionService(self.conn)
                biz = body.get("business_no")
                actor = body.get("actor_id", "CO-0001")
                if not biz:
                    return self._send(400, {"error": "缺少 business_no"})
                payload = svc.create_suggestion(biz, actor)
                payload["card_text"] = render_card(payload)
                return self._send(200, payload)

            if path == "/api/action":
                svc = SuggestionService(self.conn)
                sid = body.get("suggestion_id")
                actor = body.get("actor_id", "CO-0001")
                action = body.get("action")
                if not sid or not action:
                    return self._send(400, {"error": "缺少 suggestion_id 或 action"})
                r = svc.act(sid, actor, action,
                            after_value=body.get("after_value"),
                            basis_refs=body.get("basis_refs"),
                            signed_by=body.get("signed_by"),
                            note=body.get("note", ""))
                return self._send(200, r)

            if path == "/api/handover":
                biz = body.get("business_no")
                actor = body.get("actor_id", "CO-0001")
                if not biz:
                    return self._send(400, {"error": "缺少 business_no"})
                sug = self.conn.execute(
                    "SELECT payload FROM audit_record WHERE business_no=? AND record_type='suggestion'"
                    " ORDER BY seq DESC LIMIT 1", (biz,)).fetchone()
                sug_payload = jload(sug["payload"], {}) if sug else {}
                cur_ver = sug_payload.get("source_snapshot_version", "")
                pending = (sug_payload.get("suggestion_result") or {}).get("pending_fields") or []
                unresolved = body.get("unresolved_items")
                if unresolved is None:
                    unresolved = pending
                r = handover.create_handover(self.conn, biz, body.get("receiver"),
                                             unresolved, actor,
                                             current_version=cur_ver,
                                             processing_status="待接手")
                return self._send(200, r)

            if path == "/api/handover/confirm":
                biz = body.get("business_no")
                actor = body.get("actor_id", "CO-0001")
                if not biz:
                    return self._send(400, {"error": "缺少 business_no"})
                r = handover.confirm_handover(self.conn, biz, actor,
                                              authz.role_of(actor))
                return self._send(200, r)

            if path == "/api/replay":
                limit = int(body.get("limit", 100))
                m = replay.run_replay(self.conn, limit=limit,
                                      actor_id=body.get("actor_id", "CO-0001"))
                return self._send(200, m)

            if path == "/api/sync/heartbeat":
                sync.heartbeat(self.conn, body.get("connector", "GIS资产库"))
                return self._send(200, {"ok": True})

            return self._send(404, {"error": "未知路径", "path": path})
        except authz.AuthorizationError as e:
            return self._send(403, {"error": str(e)})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": str(e), "trace": traceback.format_exc(limit=3)})

    # ------------------------------------------------------------ 内部

    def _status(self) -> dict:
        svc = SuggestionService(self.conn)
        chain = audit.chain_status(self.conn)
        return {
            "连接器": sync.connector_status(self.conn),
            "全局提示": sync.system_banner(self.conn),
            "快照": sync.snapshot_info(self.conn),
            "哈希链": chain,
            "只读降级": chain["read_only_degraded"],
            "规则版本": "MVP-R1",
            "模型版本": "rule-baseline-v1",
            "制度性未决项": {
                "留存期限_天": 90,
                "自动删除": False,
                "双人确认": False,
                "交接约定时限_分钟": None,
                "验收抽样方法": None,
                "验收出数责任人": None,
            },
            "建议数": len(svc.list_suggestions(1000)),
        }

    @staticmethod
    def _static(name: str) -> bytes:
        p = os.path.join(WEB_DIR, name)
        if not os.path.exists(p):
            return b"<!doctype html><meta charset=utf-8><p>web/" + name.encode() + b" not found"
        with open(p, "rb") as f:
            return f.read()


def serve(conn, host: str = "127.0.0.1", port: int = 8760) -> None:
    Handler.conn = conn
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"建议服务 MVP 已启动：http://{host}:{port}/")
    print(f"  页面      /")
    print(f"  状态      /api/status")
    print(f"  指标      /api/metrics")
    print(f"  重建      /api/audit/<业务编号>")
    print(f"  链校验    /api/audit/verify")
    print("按 Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
