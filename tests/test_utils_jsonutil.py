"""utils/jsonutil.py 的测试：canonical JSON 是哈希链的基石（方案 §9.2）。"""
from __future__ import annotations

import unittest

from asset_suggestion.utils.jsonutil import jdump, jload


class TestCanonicalJson(unittest.TestCase):
    def test_key_order_does_not_matter(self):
        """字段顺序不同必须得到同一串字节，否则哈希链不可复现。"""
        a = jdump({"b": 2, "a": 1, "c": 3})
        b = jdump({"c": 3, "a": 1, "b": 2})
        self.assertEqual(a, b)

    def test_no_spaces_and_keeps_chinese(self):
        out = jdump({"名称": "饮水机", "版本": 1})
        self.assertNotIn(" ", out)
        self.assertIn("饮水机", out)

    def test_roundtrip(self):
        obj = {"business_no": "WO-202600059", "candidates": [1, 2, 3]}
        self.assertEqual(jload(jdump(obj)), obj)

    def test_jload_bad_input_returns_default(self):
        self.assertEqual(jload("{不是 json", []), [])
        self.assertEqual(jload(None, {}), {})
        self.assertEqual(jload("", {}), {})


if __name__ == "__main__":
    unittest.main()
