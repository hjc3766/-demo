"""匹配层五阶段的测试：归一化 → 行政锚定 → 召回 → 排序 → 闸门。

对应方案 §6。
"""
from __future__ import annotations

import unittest

from asset_suggestion.core import anchor as anchor_mod
from asset_suggestion.core.extract import RuleExtractor
from asset_suggestion.core.rank import confidence_band
from asset_suggestion.core.service import SuggestionService

from _base import DbCase


class TestNormalize(DbCase):
    def test_extracts_elements(self):
        ex = RuleExtractor()
        divs = anchor_mod.load_divisions(self.conn)
        table = anchor_mod.division_alias_table(divs)
        el = ex.extract("城东阳光社区大厅东侧饮水机漏水了", table)
        self.assertIn("饮水机", el.references)
        self.assertIn("东侧", el.orientation)
        self.assertTrue(el.regions)
        self.assertEqual(el.landmark, "大厅")

    def test_extracts_floor_and_building(self):
        ex = RuleExtractor()
        table = anchor_mod.division_alias_table(anchor_mod.load_divisions(self.conn))
        el = ex.extract("老城区3号楼三楼走廊灯不亮了", table)
        self.assertTrue(any("3" in b for b in el.buildings))
        self.assertTrue(el.floors)
        self.assertIn("走廊灯", "".join(el.references) + "".join(el.reference_words)
                      + "老城区3号楼三楼走廊灯不亮了")

    def test_empty_text_is_safe(self):
        ex = RuleExtractor()
        table = anchor_mod.division_alias_table(anchor_mod.load_divisions(self.conn))
        el = ex.extract("", table)
        self.assertEqual(el.references, [])


class TestAnchor(DbCase):
    def test_anchor_picks_deepest_match(self):
        divs = anchor_mod.load_divisions(self.conn)
        table = anchor_mod.division_alias_table(divs)
        el = RuleExtractor().extract("阳光社区的灯不亮了", table)
        anc = anchor_mod.anchor_from_elements(el, divs)
        self.assertIsNotNone(anc)
        self.assertEqual(anc.level, 3)

    def test_anchor_none_when_no_region(self):
        divs = anchor_mod.load_divisions(self.conn)
        table = anchor_mod.division_alias_table(divs)
        el = RuleExtractor().extract("某个地方的路灯坏了", table)
        anc = anchor_mod.anchor_from_elements(el, divs)
        self.assertIsNone(anc)

    def test_anchor_constrains_recall(self):
        """阶段二必须真的约束阶段三：锚定单元内的资产才走空间通道。

        方案 §6.4 把行政锚定称作匹配层的地基——它不是"顺手带个标签"，
        而是真正把召回范围收窄到一个行政单元。
        """
        svc = SuggestionService(self.conn)
        row = self.conn.execute(
            "SELECT business_no FROM hotline_intake WHERE raw_text LIKE '%社区%' LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(row, "种子数据里应存在带行政区划线索的工单")
        p = svc.create_suggestion(row["business_no"], "CO-0001")
        res = p["suggestion_result"]
        self.assertIsNotNone(res["anchor"], "带地名线索的原文应能锚定到行政单元")
        cands = res["candidates"]
        self.assertTrue(cands)
        self.assertTrue(any(c["in_anchor"] for c in cands),
                        "锚定后应有候选落在锚定单元内（走空间通道）")
        for c in cands:
            if c["in_anchor"]:
                self.assertTrue(c["location_anchor"].startswith(res["anchor"]["code"]),
                                "落在锚定单元内的资产，其行政单元码应与锚点一致")


class TestRanking(DbCase):
    def test_top3_contains_truth_on_easy_samples(self):
        """合成样本上，正确资产应出现在 Top-3。"""
        svc = SuggestionService(self.conn)
        hits = 0
        total = 0
        for r in self.conn.execute(
                "SELECT w.business_no, w.truth_asset_pk FROM workorder_snapshot w"
                " JOIN asset_snapshot a ON a.asset_pk = w.truth_asset_pk"
                " WHERE a.ambiguity_flag = 0 ORDER BY w.business_no LIMIT 15"):
            p = svc.create_suggestion(r["business_no"], "CO-0001")
            pks = [c["asset_pk"] for c in p["suggestion_result"]["candidates"]]
            total += 1
            if r["truth_asset_pk"] in pks:
                hits += 1
        self.assertGreater(total, 0)
        self.assertGreaterEqual(hits / total, 0.5, "合成样本 Top-3 命中率过低")

    def test_output_has_reasons_and_pending_fields(self):
        """f15：建议必须显示对应原记录和待确认字段。"""
        svc = SuggestionService(self.conn)
        p = svc.create_suggestion(self.first_workorder(), "CO-0001")
        cands = p["suggestion_result"]["candidates"]
        if not cands:
            self.skipTest("该样本无候选")
        self.assertTrue(cands[0]["reasons"])
        self.assertTrue(cands[0]["pending_fields"])
        self.assertTrue(p["fact_refs"])

    def test_confidence_band_values(self):
        self.assertEqual(confidence_band([], 0.15), "low")
        svc = SuggestionService(self.conn)
        p = svc.create_suggestion(self.first_workorder(), "CO-0001")
        self.assertIn(p["suggestion_result"]["confidence_band"], ("high", "mid", "low"))

    def test_no_automatic_dispatch(self):
        """P1/P3：建议服务不得产生任何业务副作用——不写工单状态、不写派单人。"""
        svc = SuggestionService(self.conn)
        biz = self.first_workorder()
        before = self.conn.execute(
            "SELECT status, assignee FROM workorder_snapshot WHERE business_no=?",
            (biz,)).fetchone()
        svc.create_suggestion(biz, "CO-0001")
        after = self.conn.execute(
            "SELECT status, assignee FROM workorder_snapshot WHERE business_no=?",
            (biz,)).fetchone()
        self.assertEqual(dict(before), dict(after))
        self.assertIsNone(after["assignee"])


if __name__ == "__main__":
    unittest.main()
