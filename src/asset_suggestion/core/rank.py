"""阶段四：排序与解释 —— Top-3 + 待确认字段。

对应方案 §6.6。

铁律：**LLM 只对召回候选重排与解释，不生成任何新事实。**
候选集合由空间与语义两个通道决定；LLM 的输出若包含候选集合之外的资产编号，
一律丢弃并回退到规则打分。

排序特征（与方案 §6.6 一一对应）：
    空间距离 / 要素命中 / 类别一致 / 历史相似 / 别名命中

每个候选都带 `reasons`（事实引用）与 `pending_fields`（待确认字段），
二者都会进入建议单正文——对应 f15「建议必须显示对应原记录和待确认字段」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .districts import PENDING_TEMPLATE
from .recall import Candidate
from ..utils.textutil import text_similarity

WEIGHTS = {
    "alias_hit": 0.42,
    "category_match": 0.18,
    "anchor_hit": 0.12,
    "appearance_hit": 0.12,
    "distance": 0.11,
    "history": 0.05,
}


@dataclass
class RankedCandidate:
    candidate: Candidate
    score: float
    reasons: List[dict] = field(default_factory=list)
    pending_fields: List[str] = field(default_factory=list)
    evidence_insufficient: bool = False
    llm_score: Optional[float] = None

    def to_dict(self) -> dict:
        d = self.candidate.to_dict()
        d.update({
            "score": round(self.score, 4),
            "reasons": self.reasons,
            "pending_fields": self.pending_fields,
            "evidence_insufficient": self.evidence_insufficient,
            "llm_score": self.llm_score,
        })
        return d


def build_pending_fields(candidate: Candidate, category: str) -> List[str]:
    """待确认字段：类别模板 + 该资产当前无法从系统判定的动态项。"""
    fields = list(PENDING_TEMPLATE.get(category, ["具体位置", "型号与编号"]))
    if not candidate.source_version:
        fields.append("资产资料版本缺失（source_version 为空）")
    if candidate.coord_sys not in ("WGS84", "GCJ02", "BD09", ""):
        fields.append(f"坐标系未确认（{candidate.coord_sys}）")
    if candidate.migration_version and candidate.prev_asset_code == candidate.asset_code:
        fields.append("资产曾发生迁移且编号未变，须核对是否已失效")
    return fields


def score_candidate(
    c: Candidate,
    raw_text: str,
    elements,
    anchor,
    history_count: int,
    max_dist: float,
) -> RankedCandidate:
    reasons: List[dict] = []
    score = 0.0

    # 别名命中
    alias_best = 0.0
    alias_hit_word = None
    for alias in c.alias_text:
        if alias and alias in raw_text:
            if len(alias) > alias_best:
                alias_best = 1.0
                alias_hit_word = alias
        else:
            sim = text_similarity(alias, raw_text)
            if sim > alias_best:
                alias_best = sim
                alias_hit_word = alias
    if alias_hit_word:
        w = WEIGHTS["alias_hit"] * (1.0 if alias_hit_word in raw_text else alias_best)
        score += w
        reasons.append({
            "feature": "alias_hit", "weight": round(w, 4),
            "detail": f"地标别名命中「{alias_hit_word}」", "ref": f"alias_text:{c.asset_pk}",
        })

    # 类别一致
    if elements.references and c.asset_category in elements.references:
        w = WEIGHTS["category_match"]
        score += w
        reasons.append({
            "feature": "category_match", "weight": round(w, 4),
            "detail": f"资产类别「{c.asset_category}」与报修参照物一致", "ref": f"asset_category:{c.asset_pk}",
        })

    # 行政锚定命中
    if c.in_anchor:
        w = WEIGHTS["anchor_hit"]
        score += w
        reasons.append({
            "feature": "anchor_hit", "weight": round(w, 4),
            "detail": f"落在锚定单元「{anchor.name}」内", "ref": f"location_anchor:{c.asset_pk}",
        })

    # 外观要素命中
    if elements.appearance:
        blob = " ".join(c.alias_text) + " " + c.description
        hit = [w for w in elements.appearance if w in blob]
        if hit:
            w = WEIGHTS["appearance_hit"] * min(1.0, len(hit) / 2)
            score += w
            reasons.append({
                "feature": "appearance_hit", "weight": round(w, 4),
                "detail": f"外观要素命中「{'/'.join(hit)}」", "ref": f"description:{c.asset_pk}",
            })

    # 空间距离
    if c.dist_m is not None and max_dist > 0:
        ratio = max(0.0, 1.0 - c.dist_m / max_dist)
        w = WEIGHTS["distance"] * ratio
        score += w
        reasons.append({
            "feature": "distance", "weight": round(w, 4),
            "detail": f"距锚点 {c.dist_m:.0f} 米", "ref": f"coord:{c.asset_pk}",
        })

    # 历史相似
    if history_count > 0:
        w = min(WEIGHTS["history"], 0.01 * history_count)
        score += w
        reasons.append({
            "feature": "history", "weight": round(w, 4),
            "detail": f"该资产历史同类工单 {history_count} 单", "ref": f"history:{c.asset_pk}",
        })

    rc = RankedCandidate(candidate=c, score=score, reasons=reasons)
    rc.pending_fields = build_pending_fields(c, c.asset_category)
    return rc


def rank(
    candidates: Sequence[Candidate],
    raw_text: str,
    elements,
    anchor,
    history: Dict[str, int],
    radius_m: int = 400,
) -> List[RankedCandidate]:
    ranked = [
        score_candidate(c, raw_text, elements, anchor, history.get(c.asset_pk, 0), radius_m)
        for c in candidates
    ]
    ranked.sort(key=lambda r: -r.score)
    return ranked


# ---------------------------------------------------------------- LLM 重排（受约束）

def llm_rerank(ranked: List[RankedCandidate], raw_text: str, top_k: int = 3) -> List[RankedCandidate]:
    """用本地模型重排。**只允许在给定候选内重新打分**。

    任何不在候选集合中的资产编号都会被丢弃；模型不可用时返回原排序。
    """
    from . import llm

    if not llm.available() or not ranked:
        return ranked
    cand_ids = [r.candidate.asset_pk for r in ranked]
    listing = "\n".join(
        f"- {r.candidate.asset_pk} | {r.candidate.asset_code} | {r.candidate.asset_category} "
        f"| 别名: {'、'.join(r.candidate.alias_text)} | 规则分: {r.score:.3f}"
        for r in ranked[:20]
    )
    prompt = (
        "根据报修描述，对下列候选资产重新打分。只输出 JSON。\n"
        '格式: {"scores": [{"asset_pk": "...", "score": 0.0-1.0, "reason": "一句话"}]}\n'
        "只能对给出的候选打分，禁止输出任何未列出的 asset_pk。\n\n"
        f"报修描述：{raw_text}\n"
        f"抽取要素：{elements.summary()}\n\n"
        f"候选列表：\n{listing}"
    )
    try:
        data = llm.extract_json(prompt, system="你是市政资产匹配排序器，只能在给定候选内打分。")
    except llm.LLMUnavailable:
        return ranked
    scores = {}
    reasons = {}
    if isinstance(data, dict):
        for item in data.get("scores") or []:
            if not isinstance(item, dict):
                continue
            pk = item.get("asset_pk")
            if pk not in cand_ids:      # 越界输出直接丢弃
                continue
            try:
                scores[pk] = float(item.get("score"))
            except (TypeError, ValueError):
                continue
            if isinstance(item.get("reason"), str):
                reasons[pk] = item["reason"]
    if not scores:
        return ranked
    for r in ranked:
        pk = r.candidate.asset_pk
        if pk in scores:
            r.llm_score = scores[pk]
            r.score = 0.6 * r.score + 0.4 * scores[pk]   # 与规则分加权，规则不被完全替代
            if pk in reasons:
                r.reasons.append({
                    "feature": "llm_rerank", "weight": 0.0,
                    "detail": reasons[pk], "ref": f"llm:{pk}",
                })
    ranked.sort(key=lambda r: -r.score)
    return ranked


# ---------------------------------------------------------------- 置信度分档

def confidence_band(ranked: List[RankedCandidate], delta: float = 0.15) -> str:
    """方案 §6.8：高/中/低三档，而不是单一阈值。"""
    if not ranked:
        return "low"
    if len(ranked) == 1:
        return "high"
    gap = ranked[0].score - ranked[1].score
    if gap >= delta:
        return "high"
    return "mid"
