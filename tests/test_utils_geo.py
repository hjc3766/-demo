"""utils/geo.py 的测试：PostGIS 等价语义（方案 §6.4 / §6.5）。"""
from __future__ import annotations

import unittest

from asset_suggestion.utils.geo import bbox, centroid, haversine, point_in_polygon

# 一个简单的方形多边形（云州坐标量级）
SQUARE = [[120.0, 30.0], [120.1, 30.0], [120.1, 30.1], [120.0, 30.1]]


class TestGeo(unittest.TestCase):
    def test_haversine_zero(self):
        self.assertAlmostEqual(haversine(120.0, 30.0, 120.0, 30.0), 0.0, places=6)

    def test_haversine_known_distance(self):
        """经度差 0.01° ≈ 约 960 米（纬度 30°）。"""
        d = haversine(120.0, 30.0, 120.01, 30.0)
        self.assertTrue(900 < d < 1020, d)

    def test_point_in_polygon(self):
        self.assertTrue(point_in_polygon(120.05, 30.05, SQUARE))
        self.assertFalse(point_in_polygon(120.2, 30.05, SQUARE))

    def test_boundary_point_is_not_inside(self):
        """与 PostGIS ST_Within 一致：落在边界上的点算「不在内」。"""
        self.assertFalse(point_in_polygon(120.0, 30.0, SQUARE))
        self.assertFalse(point_in_polygon(120.05, 30.0, SQUARE))

    def test_bbox(self):
        """bbox 返回 (min_lng, min_lat, max_lng, max_lat)。"""
        b = bbox(SQUARE)
        self.assertEqual(b, (120.0, 30.0, 120.1, 30.1))

    def test_centroid(self):
        lng, lat = centroid(SQUARE)
        self.assertAlmostEqual(lng, 120.05, places=6)
        self.assertAlmostEqual(lat, 30.05, places=6)


if __name__ == "__main__":
    unittest.main()
