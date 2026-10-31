"""阶段二：行政锚定 —— 把地标落到一块地上。

对应方案 §6.4。这是整个匹配层的地基：
群众说的"城东那个旧站台"不指向任何资产，但指向一片区域。
先锚定到行政单元，再在单元内做精排，比在全量资产里做语义匹配，
召回率和可解释性都高一个量级。

兼容语义鸿沟（f10）：让"群众地标"与"资产编号"两套体系在一个共同的
地理坐标空间里相遇——这是绕过编号不一致的低成本路径。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .db import jload
from ..utils.geo import bbox, centroid


@dataclass
class Anchor:
    code: str
    name: str
    level: int
    polygon: List[List[float]]
    centroid: tuple
    matched_word: Optional[str] = None

    @property
    def bbox(self):
        return bbox(self.polygon)

    def to_dict(self) -> dict:
        # polygon 一并带出：前端要画行政单元边界（阶段二锚定结果可视化），
        # 审计侧也能据此复现"这条建议锚到了哪块地"。
        return {
            "code": self.code, "name": self.name, "level": self.level,
            "centroid_lng": self.centroid[0], "centroid_lat": self.centroid[1],
            "matched_word": self.matched_word,
            "polygon": self.polygon,
        }


def load_divisions(conn) -> List[dict]:
    rows = conn.execute("SELECT * FROM admin_division").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["polygon"] = jload(d["polygon"], [])
        d["aliases"] = jload(d["aliases"], [])
        out.append(d)
    return out


def division_alias_table(divisions: Sequence[dict]) -> List[tuple]:
    """(code, name, aliases) 供抽取器反查。"""
    return [(d["code"], d["name"], list(d["aliases"])) for d in divisions]


def anchor_from_elements(elements, divisions: Sequence[dict]) -> Optional[Anchor]:
    """按要素里命中的行政区划线索选锚点。

    取**层级最深的命中**（社区优于街道优于区县），并按别名长度优先，
    避免"城东"被"东区"抢先、也避免"城中"误吞"城东区"。
    """
    by_code = {d["code"]: d for d in divisions}
    best: Optional[Anchor] = None
    for code in elements.regions:
        d = by_code.get(code)
        if not d:
            continue
        cand = Anchor(
            code=d["code"], name=d["name"], level=d["level"],
            polygon=d["polygon"], centroid=centroid(d["polygon"]),
            matched_word=next((w for w in elements.region_words if w in d["name"] or w in d["aliases"]), None),
        )
        if best is None or cand.level > best.level:
            best = cand
    return best


def asset_in_anchor(lng: float, lat: float, anchor: Anchor) -> bool:
    """资产是否落在锚定单元内（ST_Within 等价）。"""
    from ..utils.geo import point_in_polygon
    return point_in_polygon(lng, lat, anchor.polygon)
