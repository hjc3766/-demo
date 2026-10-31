"""阶段三：候选召回 —— 两个通道取并集。

对应方案 §6.5。

空间通道（对应 SQL 里的 ST_Intersects + ST_DWithin）：
    锚定单元内 或 锚点半径内，且类别一致、无歧义（f33）、未过期（f34）

语义通道（对应 pgvector 近邻）：
    用抽取要素文本与"资产别名 + 描述"做字面相似度，补回空间上不在锚定
    单元内、但描述高度相似的资产——群众说的参照物有时会跨街区
    （"就是上次修过那个"）。

**两通道取并集**得到 Top-50 候选。

SpatialIndex 是一个可替换的接口：生产替换为 PostGIS 实现，
MVP 用 SQLite + 纯 Python 实现，语义保持一致。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .anchor import Anchor, asset_in_anchor
from .db import jload
from ..utils.geo import haversine
from ..utils.textutil import text_similarity  # noqa: F401  （保留对下层的再导出）


@dataclass
class Candidate:
    asset_pk: str
    asset_code: str
    asset_category: str
    alias_text: List[str]
    description: str
    lng: float
    lat: float
    coord_sys: str
    location_anchor: Optional[str]
    migration_version: Optional[str]
    prev_lng: Optional[float]
    prev_lat: Optional[float]
    prev_asset_code: Optional[str]
    source_version: Optional[str]
    ambiguity_flag: bool
    ambiguity_reason: List[str]
    stale_flag: bool
    fetched_at: Optional[str]
    dist_m: Optional[float] = None
    in_anchor: bool = False
    channels: List[str] = field(default_factory=list)
    text_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "asset_pk": self.asset_pk, "asset_code": self.asset_code,
            "asset_category": self.asset_category, "alias_text": self.alias_text,
            "lng": self.lng, "lat": self.lat, "coord_sys": self.coord_sys,
            "location_anchor": self.location_anchor,
            "migration_version": self.migration_version,
            "source_version": self.source_version,
            "dist_m": round(self.dist_m, 1) if self.dist_m is not None else None,
            "in_anchor": self.in_anchor, "channels": self.channels,
            "text_score": round(self.text_score, 4),
        }


def _row_to_candidate(row) -> Candidate:
    d = dict(row)
    return Candidate(
        asset_pk=d["asset_pk"], asset_code=d["asset_code"],
        asset_category=d["asset_category"], alias_text=jload(d["alias_text"], []),
        description=d.get("description") or "", lng=d["lng"], lat=d["lat"],
        coord_sys=d.get("coord_sys") or "", location_anchor=d.get("location_anchor"),
        migration_version=d.get("migration_version"),
        prev_lng=d.get("prev_lng"), prev_lat=d.get("prev_lat"),
        prev_asset_code=d.get("prev_asset_code"),
        source_version=d.get("source_version"),
        ambiguity_flag=bool(d.get("ambiguity_flag")),
        ambiguity_reason=jload(d.get("ambiguity_reason"), []),
        stale_flag=bool(d.get("stale_flag")),
        fetched_at=d.get("fetched_at"),
    )


class SpatialIndex:
    """候选召回器。生产实现应换成 PostGIS（ST_Within / ST_DWithin + pgvector）。"""

    def __init__(self, conn, radius_m: int = 400, limit: int = 50,
                 semantic_threshold: float = 0.12):
        self.conn = conn
        self.radius_m = radius_m
        self.limit = limit
        self.semantic_threshold = semantic_threshold

    def query(
        self,
        categories: Sequence[str],
        anchor: Optional[Anchor],
        raw_text: str,
        elements_text: str,
    ) -> List[Candidate]:
        where, params = [], []
        if categories:
            where.append("asset_category IN (%s)" % ",".join("?" * len(categories)))
            params.extend(list(categories))
        # f33：歧义记录不进自动推荐样本；f34：超周期不参与
        where.append("ambiguity_flag = 0")
        where.append("stale_flag = 0")
        sql = "SELECT * FROM asset_snapshot"
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = self.conn.execute(sql, params).fetchall()

        spatial: List[Candidate] = []
        semantic: List[Candidate] = []
        anchor_lng = anchor.centroid[0] if anchor else None
        anchor_lat = anchor.centroid[1] if anchor else None

        for r in rows:
            c = _row_to_candidate(r)
            # 文本相似度对**所有**候选都要算：它同时是语义通道的准入依据，
            # 也是阶段四"别名命中"特征的输入。只给锚定外的资产生效会让
            # 锚定内的候选 text_score 恒为 0，进而在池截断时被挤掉。
            sim = self._semantic(c, raw_text, elements_text)
            c.text_score = sim

            if anchor is not None:
                c.in_anchor = asset_in_anchor(c.lng, c.lat, anchor)
                c.dist_m = haversine(anchor_lng, anchor_lat, c.lng, c.lat)
                near = c.dist_m <= self.radius_m
                if c.in_anchor:
                    c.channels.append("spatial")
                elif near:
                    c.channels.append("spatial_near")
                if c.in_anchor or near:
                    # 空间通道：锚定单元内或半径内，无条件进池
                    spatial.append(c)
                elif sim >= self.semantic_threshold:
                    # 语义通道：锚定外的资产靠描述相似度补回（§6.5）
                    c.channels.append("semantic")
                    semantic.append(c)
            else:
                # 无锚点时只有语义通道
                if sim >= max(self.semantic_threshold, 0.15):
                    c.channels.append("semantic")
                    semantic.append(c)

        # 空间通道按距离排序（对应 §6.5 的 ORDER BY dist_m LIMIT 50）
        spatial.sort(key=lambda c: c.dist_m if c.dist_m is not None else 1e9)
        # 语义通道按相似度排序（对应 §6.5 的向量近邻）
        semantic.sort(key=lambda c: -c.text_score)

        # 两通道取并集（§6.5）。各自保留 limit 条，池大小上限 2×limit；
        # 最终 Top-3 由阶段四的规则打分在并集上产生。
        pool = spatial[: self.limit] + semantic[: self.limit]
        pool.sort(key=lambda c: (-c.text_score, c.dist_m if c.dist_m is not None else 1e9))
        return pool

    @staticmethod
    def _semantic(c: Candidate, raw_text: str, elements_text: str) -> float:
        best = 0.0
        for alias in c.alias_text:
            best = max(best, text_similarity(alias, raw_text))
            if alias and alias in raw_text:
                best = max(best, 0.95)  # 别名原样命中
        best = max(best, text_similarity(c.description, elements_text))
        return best
