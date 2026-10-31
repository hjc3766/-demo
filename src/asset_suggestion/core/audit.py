"""留痕与审计：append-only + 哈希链 + 重建链路。

对应方案 §9 与 f45：
    每次建议保留业务编号、资料版本、事实引用、建议结果、人工改动、签核人；
    审计时应能重建「巡查报修 → 资产定位 → 派工 → 维修 → 验收 → 台账更新」
    中的这次处理；不记录账号口令或不必要的个人信息。

三条保证：
  1. 审计表在**数据库层**禁止 UPDATE / DELETE（schema.sql 触发器）
  2. 全局哈希链，篡改任一记录会导致 verify_chain() 失败
  3. 链校验失败 → 建议服务自动进入只读降级（方案 §13 S4）
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import uuid
from typing import List, Optional, Tuple

from .db import jdump, jload

GENESIS = "0" * 64

# 六节点（方案 §9.4）
NODES = ["巡查报修", "资产定位", "派工", "维修", "验收", "台账更新"]

_ACTION_TO_NODE = {
    "accept": "资产定位",
    "reject": "资产定位",
    "pause": "资产定位",
    "amend": "资产定位",
    "dispatch": "派工",
    "repair": "维修",
    "acceptance": "验收",
    "ledger": "台账更新",
}


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _hash(prev_hash: str, record: dict) -> str:
    payload = jdump(record)
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


def append(
    conn,
    record_type: str,
    business_no: str,
    payload: dict,
    suggestion_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    record_id: Optional[str] = None,
) -> dict:
    """追加一条审计记录（唯一入口）。返回含 hash 的记录头。"""
    row = conn.execute(
        "SELECT record_hash FROM audit_record ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    prev_hash = row["record_hash"] if row else GENESIS
    rid = record_id or f"{record_type[:2].upper()}-{uuid.uuid4().hex[:12]}"
    created_at = now_iso()
    body = {
        "record_id": rid,
        "record_type": record_type,
        "business_no": business_no,
        "suggestion_id": suggestion_id,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "payload": payload,
        "created_at": created_at,
    }
    rh = _hash(prev_hash, body)
    conn.execute(
        "INSERT INTO audit_record (record_id, record_type, business_no, suggestion_id,"
        " actor_id, actor_role, payload, created_at, prev_hash, record_hash)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (rid, record_type, business_no, suggestion_id, actor_id, actor_role,
         jdump(payload), created_at, prev_hash, rh),
    )
    conn.commit()
    _reindex(conn, business_no)
    return {
        "record_id": rid, "seq": conn.execute("SELECT last_insert_rowid() AS s").fetchone()["s"],
        "created_at": created_at, "prev_hash": prev_hash, "record_hash": rh,
    }


def verify_chain(conn) -> Tuple[bool, Optional[int], str]:
    """校验全局哈希链。返回 (是否通过, 断链位置, 说明)。"""
    prev = GENESIS
    rows = conn.execute(
        "SELECT seq, record_id, record_type, business_no, suggestion_id, actor_id,"
        " actor_role, payload, created_at, prev_hash, record_hash"
        " FROM audit_record ORDER BY seq"
    ).fetchall()
    for r in rows:
        d = dict(r)
        if d["prev_hash"] != prev:
            return False, d["seq"], f"seq {d['seq']} 的 prev_hash 与上一条不衔接"
        body = {
            "record_id": d["record_id"], "record_type": d["record_type"],
            "business_no": d["business_no"], "suggestion_id": d["suggestion_id"],
            "actor_id": d["actor_id"], "actor_role": d["actor_role"],
            "payload": jload(d["payload"], {}), "created_at": d["created_at"],
        }
        expect = _hash(prev, body)
        if expect != d["record_hash"]:
            return False, d["seq"], f"seq {d['seq']} 记录哈希与内容不符（内容疑似被篡改）"
        prev = d["record_hash"]
    return True, None, f"链完整，共 {len(rows)} 条记录"


def business_chain(conn, business_no: str) -> List[dict]:
    rows = conn.execute(
        "SELECT seq, record_id, record_type, business_no, suggestion_id, actor_id,"
        " actor_role, payload, created_at, record_hash"
        " FROM audit_record WHERE business_no = ? ORDER BY seq",
        (business_no,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = jload(d["payload"], {})
        out.append(d)
    return out


def rebuild(conn, business_no: str) -> dict:
    """按业务编号重建单次处理链路，并映射到六节点（f45 / 方案 §9.4）。

    节点证据有两类：
      * 审计记录（建议 / 人工动作）——本服务产生
      * 源系统镜像（热线受理 / 工单状态）——只读证据，
        用于补齐"巡查报修"这类不产生审计记录的节点
    """
    chain = business_chain(conn, business_no)
    nodes = {n: [] for n in NODES}
    evidence = {n: [] for n in NODES}
    for rec in chain:
        ptype = rec["record_type"]
        payload = rec["payload"]
        if ptype == "suggestion":
            nodes["资产定位"].append(rec["record_id"])
            evidence["资产定位"].append("建议记录")
        elif ptype == "human_action":
            node = _ACTION_TO_NODE.get(payload.get("action", ""), "资产定位")
            nodes[node].append(rec["record_id"])
            evidence[node].append(f"人工动作:{payload.get('action')}")
        elif ptype == "handover":
            pass  # 交接是跨节点事件，记录在链路里但不占用节点

    # 源系统镜像证据
    intake = conn.execute(
        "SELECT intake_id, received_at FROM hotline_intake WHERE business_no=?"
        " ORDER BY received_at LIMIT 1", (business_no,)).fetchone()
    if intake:
        nodes["巡查报修"].append(intake["intake_id"])
        evidence["巡查报修"].append(f"热线受理:{intake['intake_id']}")
    wo = conn.execute(
        "SELECT status, community_done_at, repair_done_at, ledger_done_at"
        "  FROM workorder_snapshot WHERE business_no=?", (business_no,)).fetchone()
    if wo:
        d = dict(wo)
        for node, col in (("验收", "community_done_at"), ("维修", "repair_done_at"),
                          ("台账更新", "ledger_done_at")):
            if d.get(col):
                evidence[node].append(f"工单镜像:{col}={d[col]}")
                nodes[node].append(f"WO:{business_no}:{col}")

    covered = [n for n in NODES if nodes[n]]
    return {
        "business_no": business_no,
        "record_count": len(chain),
        "chain": chain,
        "node_path": nodes,
        "node_evidence": evidence,
        "nodes_covered": covered,
        "nodes_missing": [n for n in NODES if n not in covered],
    }


def _reindex(conn, business_no: str) -> None:
    """维护重建索引（便于按业务编号快速检索）。"""
    chain = business_chain(conn, business_no)
    conn.execute(
        "INSERT INTO audit_index (business_no, chain_ids, node_path, updated_at)"
        " VALUES (?,?,?,?) ON CONFLICT(business_no) DO UPDATE SET"
        " chain_ids=excluded.chain_ids, node_path=excluded.node_path, updated_at=excluded.updated_at",
        (business_no, jdump([c["record_id"] for c in chain]),
         jdump({n: [] for n in NODES}), now_iso()),
    )
    conn.commit()


def chain_status(conn) -> dict:
    """链校验 + 只读降级开关（S4 的自动响应）。"""
    ok, broken_at, detail = verify_chain(conn)
    return {
        "ok": ok,
        "broken_at": broken_at,
        "detail": detail,
        "read_only_degraded": not ok,
    }


def retention_due(conn, retention_days: int) -> List[dict]:
    """到期待复核清单（f43：删除不自动，由安全监督员逐条决定删除或归档）。"""
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=retention_days)
    rows = conn.execute(
        "SELECT business_no, record_type, COUNT(*) AS n, MIN(created_at) AS oldest"
        " FROM audit_record GROUP BY business_no, record_type"
    ).fetchall()
    due = []
    for r in rows:
        d = dict(r)
        try:
            t = _dt.datetime.fromisoformat(d["oldest"])
            if t.tzinfo is None:
                t = t.replace(tzinfo=_dt.timezone.utc)
        except (ValueError, TypeError):
            continue
        if t <= cutoff:
            due.append(d)
    return due
