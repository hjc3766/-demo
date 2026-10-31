"""utils/textutil.py 的测试：字符 bigram 相似度。

对应方案 §4.1 / §6.5：这是 pgvector 的 MVP 近似。
"""
from __future__ import annotations

import unittest

from asset_suggestion.utils.textutil import text_similarity


class TestTextSimilarity(unittest.TestCase):
    def test_identical_is_one(self):
        self.assertEqual(text_similarity("楼道门口", "楼道门口"), 1.0)

    def test_empty_is_zero(self):
        self.assertEqual(text_similarity("", "楼道门口"), 0.0)
        self.assertEqual(text_similarity("楼道门口", ""), 0.0)

    def test_partial_overlap_between(self):
        sim = text_similarity("楼道门口的饮水机", "楼道门口路灯")
        self.assertGreater(sim, 0.0)
        self.assertLess(sim, 1.0)

    def test_unrelated_is_zero(self):
        self.assertEqual(text_similarity("楼道门口", "zzzzzz"), 0.0)

    def test_bigram_depends_on_order(self):
        """bigram 是相邻字符对，字序变了相似度就会变——这是已知局限。

        真实语义召回要靠 pgvector（方案 §4.1），本近似只在 MVP 里兜底。
        """
        self.assertLess(text_similarity("门口楼道", "楼道门口"), 1.0)
        self.assertGreater(text_similarity("门口楼道", "楼道门口"), 0.0)


if __name__ == "__main__":
    unittest.main()
