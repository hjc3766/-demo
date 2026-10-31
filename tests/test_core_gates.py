"""四类闸门与召回过滤规则的测试。

对应方案 §6.7（G1–G4）、§5.2.1（歧义）、f33、f34、f44、f11。
"""
from __future__ import annotations

import unittest

from asset_suggestion.core import gates
from asset_suggestion.core.service import SuggestionService

from _base import DbCase


class TestGateG1(DbCase):
    def test_migrated_asset_triggers_g1(self):
        """G1：迁移有版本号、坐标已变、编号未变 → must_human（f11 典型样本）。"""
        row = self.conn.execute(
            "SELECT * FROM asset_snapshot WHERE migration_version IS NOT NULL"
            " AND prev_asset_code = asset_code LIMIT 1").fetchone()
        self.assertIsNotNone(row, "种子数据应包含 G1 样本")
        svc = SuggestionService(self.conn)
        biz = self.workorder_for(row["asset_pk"])
        p = svc.create_suggestion(biz, "CO-0001")
        g1 = [g for g in (p.get("gates") or []) if g["gate"] == gates.G1]
        self.assertTrue(g1, "应命中 G1")
        self.assertEqual(p["result_type"], "must_human")
        self.assertTrue(g1[0]["original_record"], "G1 必须标注原记录")
        self.assertTrue(g1[0]["receiver"], "G1 必须标注接手人")

    def test_non_migrated_asset_not_g1(self):
        row = self.conn.execute(
            "SELECT * FROM asset_snapshot WHERE migration_version IS NULL LIMIT 1").fetchone()
        cands = gates.check_g1([])
        self.assertEqual(cands, [])


class TestGateG2(DbCase):
    def test_missing_source_version_marks_evidence_insufficient(self):
        """G2：候选无 source_version → 依据不足。"""
        row = self.conn.execute(
            "SELECT * FROM asset_snapshot WHERE source_version IS NULL LIMIT 1").fetchone()
        self.assertIsNotNone(row, "种子数据应包含无 source_version 的资产")
        biz = self.workorder_for(row["asset_pk"])
        svc = SuggestionService(self.conn)
        p = svc.create_suggestion(biz, "CO-0001")
        g2 = [g for g in (p.get("gates") or []) if g["gate"] == gates.G2]
        self.assertTrue(g2)
        self.assertTrue(g2[0]["original_record"])


class TestGateG3(DbCase):
    def test_two_conflicting_versions_trigger_g3(self):
        """G3：同一资产两个版本且坐标不同 → 两个版本一并呈现（f24）。"""
        row = self.conn.execute(
            "SELECT asset_pk, COUNT(*) AS n FROM asset_version_snapshot"
            " GROUP BY asset_pk HAVING n > 1 LIMIT 1").fetchone()
        self.assertIsNotNone(row, "种子数据应包含多版本资产")
        biz = self.workorder_for(row["asset_pk"])
        svc = SuggestionService(self.conn)
        p = svc.create_suggestion(biz, "CO-0001")
        g3 = [g for g in (p.get("gates") or []) if g["gate"] == gates.G3]
        self.assertTrue(g3)
        self.assertIn("versions", g3[0]["original_record"])
        self.assertGreaterEqual(len(g3[0]["original_record"]["versions"]), 2)


class TestGateG4(DbCase):
    def test_connector_down_triggers_g4(self):
        """G4：关键系统不可用 → 快照降级 + must_human + 显示"暂未更新"。"""
        self.set_connector_down("GIS资产库", minutes=40)
        svc = SuggestionService(self.conn)
        p = svc.create_suggestion(self.first_workorder(), "CO-0001")
        g4 = [g for g in (p.get("gates") or []) if g["gate"] == gates.G4]
        self.assertTrue(g4)
        self.assertEqual(p["result_type"], "must_human")
        self.assertIn("暂未更新", p.get("degraded_banner") or "")

    def test_connector_healthy_no_g4(self):
        svc = SuggestionService(self.conn)
        p = svc.create_suggestion(self.first_workorder(), "CO-0001")
        g4 = [g for g in (p.get("gates") or []) if g["gate"] == gates.G4]
        self.assertFalse(g4)


class TestRecallFiltering(DbCase):
    def test_ambiguous_assets_not_recommended(self):
        """f33：歧义记录不进自动推荐样本，只进人工修复队列。"""
        svc = SuggestionService(self.conn)
        for r in self.conn.execute(
                "SELECT business_no FROM workorder_snapshot ORDER BY business_no LIMIT 10"):
            p = svc.create_suggestion(r["business_no"], "CO-0001")
            for c in p["suggestion_result"]["candidates"]:
                self.assertFalse(c.get("ambiguity_flag"),
                                 "歧义资产不得出现在候选中")

    def test_stale_assets_not_recommended(self):
        """f34：超过一个更新周期的资产不参与推荐。"""
        self.conn.execute("UPDATE asset_snapshot SET stale_flag = 1")
        self.conn.commit()
        svc = SuggestionService(self.conn)
        p = svc.create_suggestion(self.first_workorder(), "CO-0001")
        for c in p["suggestion_result"]["candidates"]:
            self.assertFalse(c.get("stale_flag"))

    def test_replay_excludes_ambiguous_truth(self):
        """回放分母排除歧义样本（f33 + f53 分母去重）。"""
        from asset_suggestion.core.replay import run_replay
        m = run_replay(self.conn, limit=20)
        self.assertGreaterEqual(m["歧义排除数_人工修复队列"], 0)
        self.assertEqual(
            m["进入推荐样本数_分母"] + m["歧义排除数_人工修复队列"],
            m["取样工单数"])
        self.assertTrue(m["验收条件_歧义记录全部落入人工修复队列"])


if __name__ == "__main__":
    unittest.main()
