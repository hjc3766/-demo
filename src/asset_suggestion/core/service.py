"""业务编排层：把匹配层与治理层串成一次完整的"出建议"。

对应方案 §3.2 时序图与 §8 三道闸门。

本层保证三条架构承诺（方案 §1.3）：
  P1 建议服务永远不产生业务副作用 —— 本模块没有任何写向业务系统的路径，
     只写自建的 audit_record。
  P2 建议永远不覆盖历史       —— 全部经 audit.append（append-only + 哈希链）。
  P3 AI 输出永远不被当作决定 —— 建议记录带 result_type 与 trigger_reason，
     派单由授权岗位在原系统确认。
"""
from __future__ import annotations

import hashlib
from typing import List, Optional

from . import anchor as anchor_mod
from . import audit, authz, gates, handover, sync
from .db import jdump, jload
from .extract import default_extractor
from .rank import confidence_band, llm_rerank, rank
from .recall import SpatialIndex

RULE_VERSION = "MVP-R1"
MODEL_VERSION = "rule-baseline-v1"

_BLOCKING_GATES = {gates.G1, gates.G3, gates.G4}


class SuggestionService:
    def __init__(self, conn, extractor=None, index=None):
        self.conn = conn
        self.extractor = extractor or default_extractor()
        self.index = index or SpatialIndex(
            conn, radius_m=CONFIG_RADIUS(), limit=CONFIG_LIMIT()
        )

    # ------------------------------------------------------------ 主流程

    def create_suggestion(self, business_no: str, actor_id: str) -> dict:
        # 0) 链完整性优先：断链则只读降级，不再产出新建议（方案 §13 S4）
        st = audit.chain_status(self.conn)
        if not st["ok"]:
            raise RuntimeError(f"哈希链校验失败，服务进入只读降级：{st['detail']}")

        role = authz.role_of(actor_id)
        intake = self.conn.execute(
            "SELECT * FROM hotline_intake WHERE business_no = ? ORDER BY received_at DESC LIMIT 1",
            (business_no,)).fetchone()
        if intake is None:
            raise ValueError(f"业务编号 {business_no} 没有受理记录")
        intake = dict(intake)
        wo_row = self.conn.execute(
            "SELECT * FROM workorder_snapshot WHERE business_no = ?", (business_no,)).fetchone()
        wo = dict(wo_row) if wo_row else {}

        raw_text = intake.get("raw_text") or ""
        photos = jload(intake.get("photos"), [])

        # ① 归一化
        divisions = anchor_mod.load_divisions(self.conn)
        alias_table = anchor_mod.division_alias_table(divisions)
        elements = self.extractor.extract(raw_text, alias_table)
        elements_text = elements.summary()

        # ② 行政锚定
        anc = anchor_mod.anchor_from_elements(elements, divisions)

        # ③ 候选召回（空间 + 语义并集）
        categories = list(elements.references)
        candidates = self.index.query(categories, anc, raw_text, elements_text)

        # ④ 排序与解释
        history = self._history_map()
        ranked = rank(candidates, raw_text, elements, anc, history, radius_m=CONFIG_RADIUS())
        ranked = llm_rerank(ranked, raw_text, top_k=3)
        band = confidence_band(ranked, delta=0.15)

        # ⑤ 闸门判定（只针对将要呈现的 Top-K 候选；G4 为全局闸门）
        top_k = CONFIG_LIMIT_TOP_K()
        hits = gates.evaluate(self.conn, ranked[:top_k], sync_period_minutes=15)
        blocking = [h for h in hits if h.gate in _BLOCKING_GATES]

        if not ranked:
            result_type = "insufficient"
            band = "low"
        elif blocking:
            result_type = "must_human"
            # 注意：**不**把置信分档压成 low。
            # 方案 §6.8 中"低"专指"无候选或要素冲突"；
            # 转人工是闸门结论，与匹配置信度是两个独立维度。
        else:
            result_type = "suggest"

        top = [r.to_dict() for r in ranked[:3]]
        pending = []
        for r in ranked[:3]:
            for f in r.pending_fields:
                if f not in pending:
                    pending.append(f)

        snapshot_version = self._snapshot_version(ranked, intake)

        payload = {
            "business_no": business_no,
            "source_snapshot_version": snapshot_version,
            "fact_refs": self._fact_refs(ranked, elements, anc),
            "suggestion_result": {
                "candidates": top,
                "pending_fields": pending,
                "confidence_band": band,
                "anchor": anc.to_dict() if anc else None,
                "elements": elements.to_dict(),
                "extraction_summary": elements_text,
                "photos": photos,
                "recall_count": len(candidates),
            },
            "human_amendments": [],
            "signed_by": None,
            "result_type": result_type,
            "trigger_reason": blocking[0].gate if blocking else None,
            "gates": [h.to_dict() for h in hits],
            "rule_version": RULE_VERSION,
            "model_version": MODEL_VERSION,
            "extractor": getattr(self.extractor, "name", "rule"),
            "degraded": bool(sync.system_banner(self.conn)),
            "degraded_banner": sync.system_banner(self.conn),
        }

        head = audit.append(
            self.conn, "suggestion", business_no, payload,
            actor_id=actor_id, actor_role=role,
        )
        payload["suggestion_id"] = head["record_id"]
        payload["created_at"] = head["created_at"]
        payload["record_hash"] = head["record_hash"]
        return payload

    # ------------------------------------------------------------ 人工动作

    def act(
        self,
        suggestion_id: str,
        actor_id: str,
        action: str,
        after_value=None,
        basis_refs=None,
        signed_by: Optional[str] = None,
        note: str = "",
    ) -> dict:
        role = authz.role_of(actor_id)
        authz.require(action, role)
        sug = self.get_suggestion(suggestion_id)
        if sug is None:
            raise ValueError(f"建议 {suggestion_id} 不存在")

        payload = {
            "action": action,
            "before_value": sug.get("suggestion_result"),
            "after_value": after_value,
            "basis_refs": list(basis_refs or []),
            "signed_by": signed_by,
            "note": note,
            "suggestion_result_type": sug.get("result_type"),
        }
        head = audit.append(
            self.conn, "human_action", sug["business_no"], payload,
            suggestion_id=suggestion_id, actor_id=actor_id, actor_role=role,
        )
        return {"action_id": head["record_id"], "created_at": head["created_at"], **payload}

    # ------------------------------------------------------------ 查询

    def get_suggestion(self, suggestion_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM v_suggestion WHERE suggestion_id = ?", (suggestion_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["payload"] = jload(d["payload"], {})
        return d

    def list_suggestions(self, limit: int = 50) -> List[dict]:
        rows = self.conn.execute(
            "SELECT * FROM v_suggestion ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = jload(d["payload"], {})
            out.append(d)
        return out

    def rebuild(self, business_no: str) -> dict:
        return audit.rebuild(self.conn, business_no)

    # ------------------------------------------------------------ 内部

    def _history_map(self) -> dict:
        rows = self.conn.execute(
            "SELECT truth_asset_pk AS pk, COUNT(*) AS n FROM workorder_snapshot"
            " WHERE truth_asset_pk IS NOT NULL GROUP BY truth_asset_pk"
        ).fetchall()
        return {r["pk"]: r["n"] for r in rows}

    @staticmethod
    def _snapshot_version(ranked, intake) -> str:
        parts = [f"intake:{intake.get('intake_id')}:{intake.get('received_at')}"]
        for r in ranked[:20]:
            c = r.candidate
            parts.append(f"{c.asset_pk}@{c.source_version or '-'}:{c.lng:.6f},{c.lat:.6f}")
        return "v1-" + hashlib.sha256("|".join(sorted(parts)).encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _fact_refs(ranked, elements, anc) -> List[dict]:
        refs = [{
            "feature": "extraction",
            "detail": elements.summary(),
            "ref": "hotline_intake.raw_text",
        }]
        if anc:
            refs.append({
                "feature": "admin_anchor",
                "detail": f"行政锚定：{anc.name}（{anc.code}）",
                "ref": f"admin_division:{anc.code}",
            })
        for r in ranked[:3]:
            for reason in r.reasons:
                refs.append({"feature": reason["feature"], "detail": reason["detail"],
                             "ref": reason.get("ref", "")})
        return refs


# ---------------------------------------------------------------- 建议单渲染

def render_card(payload: dict) -> str:
    """按方案 §6.6 的输出规格渲染成人类可读的建议单。"""
    res = payload.get("suggestion_result", {})
    lines = []
    lines.append(f"建议编号：{payload.get('suggestion_id', '（未落库）')}")
    lines.append(f"业务编号：{payload.get('business_no')}")
    lines.append(f"资料版本：{payload.get('source_snapshot_version')}")
    lines.append(f"规则版本：{payload.get('rule_version')}    模型版本：{payload.get('model_version')}")
    lines.append(f"抽取要素：{res.get('extraction_summary', '')}")
    anc = res.get("anchor")
    if anc:
        lines.append(f"行政锚定：{anc['name']}（{anc['code']}）")
    lines.append(f"置信分档：{res.get('confidence_band')}    召回候选：{res.get('recall_count')}")
    lines.append("")
    cands = res.get("candidates", [])
    if not cands:
        lines.append("候选：（无）")
    for i, c in enumerate(cands, 1):
        lines.append(f"候选 {i}（置信度：{c.get('score')}）")
        lines.append(f"  资产：{c.get('asset_code')} / {c.get('asset_pk')}"
                     f"   坐标：({c.get('lng')}, {c.get('lat')})   类别：{c.get('asset_category')}")
        lines.append(f"  依据：{'; '.join(r.get('detail','') for r in c.get('reasons', [])) or '（无）'}")
        lines.append(f"  待确认：{'; '.join(c.get('pending_fields', [])) or '（无）'}")
    if res.get("pending_fields"):
        lines.append("")
        lines.append(f"整体待确认字段：{'; '.join(res['pending_fields'])}")
    gs = payload.get("gates") or []
    if gs:
        lines.append("")
        lines.append("【转人工提示】")
        for g in gs:
            lines.append(f"  触发原因：{g['gate']} {g['label']}")
            lines.append(f"  说明：{g['detail']}")
            lines.append(f"  接手人：{g.get('receiver') or '（未指定，须由规则指定，不得默认指派）'}")
            if g.get("original_record"):
                lines.append(f"  原记录：{jdump(g['original_record'])}")
    if payload.get("degraded_banner"):
        lines.append("")
        lines.append(f"【系统状态】{payload['degraded_banner']}")
    lines.append("")
    lines.append("※ 本建议仅供参考，派单须由授权岗位在原系统确认；本服务不产生任何业务副作用。")
    return "\n".join(lines)


def CONFIG_LIMIT_TOP_K() -> int:
    from .config import CONFIG
    return CONFIG.top_k


def CONFIG_RADIUS() -> int:
    from .config import CONFIG
    return CONFIG.recall_radius_m


def CONFIG_LIMIT() -> int:
    from .config import CONFIG
    return CONFIG.recall_limit
