"""几何工具：距离、点面关系、包围盒。

MVP 用纯 Python 实现；生产应替换为 PostGIS（方案 §4.1 选型 postgis/postgis GPL-2.0）。
本模块是 SpatialIndex 的支撑，接口保持与 PostGIS 语义一致：
  - ST_Within  → point_in_polygon
  - ST_DWithin → within_distance
  - ST_Distance → haversine
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

EARTH_RADIUS_M = 6371008.8  # IUGG 平均地球半径


def haversine(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """两点球面距离（米）。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def within_distance(lng1: float, lat1: float, lng2: float, lat2: float, radius_m: float) -> bool:
    """ST_DWithin 等价物。"""
    return haversine(lng1, lat1, lng2, lat2) <= radius_m


def _on_segment(lng: float, lat: float, x1: float, y1: float, x2: float, y2: float) -> bool:
    """点是否落在线段上（含端点），带一个极小的浮点容差。"""
    cross = (lng - x1) * (y2 - y1) - (lat - y1) * (x2 - x1)
    if abs(cross) > 1e-9:
        return False
    if min(x1, x2) - 1e-9 <= lng <= max(x1, x2) + 1e-9:
        return min(y1, y2) - 1e-9 <= lat <= max(y1, y2) + 1e-9
    return False


def point_in_polygon(lng: float, lat: float, polygon: Sequence[Sequence[float]]) -> bool:
    """射线法判断点是否落在多边形内（ST_Within 等价物）。

    与 PostGIS `ST_Within` 保持一致：**落在边界上的点算「不在内」**。
    纯射线法会把顶点算成内部，所以先做一次边界判定。
    """
    if not polygon or len(polygon) < 3:
        return False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i][0], polygon[i][1]
        x2, y2 = polygon[(i + 1) % n][0], polygon[(i + 1) % n][1]
        if _on_segment(lng, lat, x1, y1, x2, y2):
            return False
    inside = False
    for i in range(n):
        x1, y1 = polygon[i][0], polygon[i][1]
        x2, y2 = polygon[(i + 1) % n][0], polygon[(i + 1) % n][1]
        # 判断边是否横跨水平射线
        if (y1 > lat) != (y2 > lat):
            x_at = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lng < x_at:
                inside = not inside
    return inside


def bbox(polygon: Sequence[Sequence[float]]) -> Tuple[float, float, float, float]:
    """返回 (min_lng, min_lat, max_lng, max_lat)。"""
    lngs = [p[0] for p in polygon]
    lats = [p[1] for p in polygon]
    return min(lngs), min(lats), max(lngs), max(lats)


def bbox_contains(box: Tuple[float, float, float, float], lng: float, lat: float) -> bool:
    min_lng, min_lat, max_lng, max_lat = box
    return min_lng <= lng <= max_lng and min_lat <= lat <= max_lat


def centroid(polygon: Sequence[Sequence[float]]) -> Tuple[float, float]:
    """多边形几何中心（顶点平均）。"""
    if not polygon:
        return (0.0, 0.0)
    return (
        sum(p[0] for p in polygon) / len(polygon),
        sum(p[1] for p in polygon) / len(polygon),
    )


def bbox_radius_m(box: Tuple[float, float, float, float]) -> float:
    """包围盒外接圆半径（米），用于给锚定单元一个粗略半径。"""
    min_lng, min_lat, max_lng, max_lat = box
    cx, cy = (min_lng + max_lng) / 2, (min_lat + max_lat) / 2
    return haversine(cx, cy, max_lng, max_lat)
