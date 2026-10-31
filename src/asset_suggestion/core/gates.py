"""阶段五：四类强制转人工闸门 + 闸门外的附带判定。

对应方案 §6.7 与 f44（原文）：出现
  ① 道路改造迁移灯杆后编号未变、工单被派到旧坐标
  ② 资料缺少依据
  ③ 两个版本冲突
  ④ 关键系统不可用
——这四类必须转人工，且须标注触发原因、接手人与确认时间。

判定逻辑完全自建且可审计（方案 §4.2 判断二）：不用通用规则引擎黑箱，
每条判定都能追溯到具体规则版本与字段取值。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .rank import RankedCandidate

G1 = "G1_迁移编号未变坐标已变"
G2 = "G2_资料缺少依据"
G3 = "G3_两个版本冲突"
G4 = "G4_关键系统不可用"

GATE_LABELS = {
    G1: "迁移后编号未变、坐标已变",
    G2: "资料缺少依据",
    G3: "两个版本冲突",
    G4: "关键系统不可用",
}

# 接手人推导规则（f44 要求标注接手人；f23 要求明确下一责任人）
RECEIVER_RULES = {
    "G1": "维修协调员",     # f15：命中迁移场景由维修协调员核对后继续
    "G2": "维修协调员",
    "G3": "设施安全监督员",  # f24：规则冲突附两版本给安全监督员
    "G4": "维修协调员",
}


@dataclass
class GateHit:
    gate: str
    label: str
    detail: str
    asset_pk: Optional[str] = None
    original_record: Optional[dict] = None
    receiver: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "gate": self.gate, "label": self.label, "detail": self.detail,
            "asset_pk": self.asset_pk, "original_record": self.original_record,
            "receiver": self.receiver, **self.extra,
        }


def check_g1(ranked: List[RankedCandidate]) -> List[GateHit]:
    """G1：migration_version 非空 且 prev_coord ≠ 当前坐标 且 asset_code 未变更。"""
    hits = []
    for r in ranked:
        c = r.candidate
        if not c.migration_version:
            continue
        if c.prev_lng is None or c.prev_lat is None:
            continue
        moved = (abs(c.prev_lng - c.lng) > 1e-6) or (abs(c.prev_lat - c.lat) > 1e-6)
        code_unchanged = (c.prev_asset_code is not None and c.prev_asset_code == c.asset_code)
        if moved and code_unchanged:
            hits.append(GateHit(
                gate=G1, label=GATE_LABELS[G1],
                detail=(f"资产「{c.asset_code}」迁移版本 {c.migration_version}，"
                        f"坐标已由 ({c.prev_lng:.5f},{c.prev_lat:.5f}) 变为 ({c.lng:.5f},{c.lat:.5f})，"
                        f"但业务编号未变——命中 f11 的典型失败样本"),
                asset_pk=c.asset_pk,
                original_record={
                    "asset_pk": c.asset_pk, "asset_code": c.asset_code,
                    "prev_coord": [c.prev_lng, c.prev_lat], "current_coord": [c.lng, c.lat],
                    "migration_version": c.migration_version,
                },
                receiver=RECEIVER_RULES["G1"],
            ))
    return hits


def check_g2(ranked: List[RankedCandidate]) -> List[GateHit]:
    """G2：候选资产 source_version 为空，或事实引用不足以支撑该候选。"""
    hits = []
    for r in ranked:
        c = r.candidate
        if not c.source_version:
            r.evidence_insufficient = True
            hits.append(GateHit(
                gate=G2, label=GATE_LABELS[G2],
                detail=f"候选「{c.asset_code}」无源系统资料版本（source_version 为空），依据不足",
                asset_pk=c.asset_pk,
                original_record={"asset_pk": c.asset_pk, "asset_code": c.asset_code,
                                 "source_version": c.source_version},
                receiver=RECEIVER_RULES["G2"],
            ))
        elif not r.reasons:
            r.evidence_insufficient = True
            hits.append(GateHit(
                gate=G2, label=GATE_LABELS[G2],
                detail=f"候选「{c.asset_code}」无任何可引用的事实依据",
                asset_pk=c.asset_pk,
                original_record={"asset_pk": c.asset_pk, "asset_code": c.asset_code},
                receiver=RECEIVER_RULES["G2"],
            ))
    return hits


def check_g3(conn, ranked: List[RankedCandidate]) -> List[GateHit]:
    """G3：同一 asset_pk 存在两条不同 source_version 且坐标不同 → 两个版本一并呈现。"""
    hits = []
    for r in ranked:
        pk = r.candidate.asset_pk
        rows = conn.execute(
            "SELECT source_version, lng, lat, coord_sys, fetched_at "
            "FROM asset_version_snapshot WHERE asset_pk = ? ORDER BY fetched_at", (pk,)
        ).fetchall()
        if len(rows) < 2:
            continue
        versions = [dict(x) for x in rows]
        distinct = {(round(v["lng"], 6), round(v["lat"], 6)) for v in versions if v["lng"] is not None}
        if len(distinct) > 1:
            hits.append(GateHit(
                gate=G3, label=GATE_LABELS[G3],
                detail=(f"资产「{pk}」在快照中存在 {len(versions)} 个版本且坐标不一致——"
                        f"按 f24 两个版本一并附呈，不自行合并、不挑宽松条款"),
                asset_pk=pk,
                original_record={"versions": versions},
                receiver=RECEIVER_RULES["G3"],
            ))
    return hits


def check_g4(conn, sync_period_minutes: int) -> List[GateHit]:
    """G4：连接器心跳失败 ≥ 1 个同步周期 → 走快照降级并转人工。"""
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    hits = []
    for row in conn.execute("SELECT connector, last_heartbeat, status FROM connector_state"):
        d = dict(row)
        hb = d["last_heartbeat"]
        stale = True
        if hb:
            try:
                t = _dt.datetime.fromisoformat(hb)
                if t.tzinfo is None:
                    t = t.replace(tzinfo=_dt.timezone.utc)
                age_min = (now - t).total_seconds() / 60.0
                stale = age_min >= sync_period_minutes
            except ValueError:
                stale = True
        if stale:
            hits.append(GateHit(
                gate=G4, label=GATE_LABELS[G4],
                detail=(f"连接器「{d['connector']}」心跳已失效（状态 {d['status']}），"
                        f"超过 {sync_period_minutes} 分钟未更新——当前使用带时间戳的只读快照"),
                receiver=RECEIVER_RULES["G4"],
                extra={"connector": d["connector"], "snapshot_hint": "暂未更新"},
            ))
    return hits


def evaluate(conn, ranked: List[RankedCandidate], sync_period_minutes: int = 15) -> List[GateHit]:
    """执行四类闸门。

    **只对将要呈现给用户的 Top-K 候选执行资产级闸门（G1/G2/G3）**——
    闸门的作用是判断"我们即将给出的这条建议能不能直接用"，
    而不是"召回池里有没有一个可疑资产"。否则只要召回池中混入一个迁移资产，
    每一单都会被判为必须转人工。

    G4 是全局性的（连接器不可用），对所有候选生效。
    """
    hits: List[GateHit] = []
    hits += check_g4(conn, sync_period_minutes)
    hits += check_g1(ranked)
    hits += check_g3(conn, ranked)
    hits += check_g2(ranked)
    return hits
