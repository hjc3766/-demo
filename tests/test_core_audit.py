"""P2「建议永远不覆盖历史」的测试。

对应方案 §9.1 写入策略、§9.2 哈希链、f45 / f15。
"""
from __future__ import annotations

import sqlite3
import unittest

from asset_suggestion.core import audit
from asset_suggestion.core.service import SuggestionService

from _base import DbCase


class TestAppendOnly(DbCase):
    def setUp(self):
        super().setUp()
        # 触发器是行级的：表里有记录才会触发。先产生一条建议。
        self.svc = SuggestionService(self.conn)
        self.biz = self.first_workorder()
        self.svc.create_suggestion(self.biz, "CO-0001")

    def test_update_denied(self):
        """审计表在数据库层禁止 UPDATE。"""
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.conn.execute("UPDATE audit_record SET payload = '{}'")
        self.assertIn("append-only", str(cm.exception))

    def test_delete_denied(self):
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.conn.execute("DELETE FROM audit_record")
        self.assertIn("append-only", str(cm.exception))

    def test_insert_still_allowed(self):
        """INSERT 仍然允许——这是唯一的合法写入路径。"""
        before = self.conn.execute("SELECT COUNT(*) AS n FROM audit_record").fetchone()["n"]
        self.svc.act(self.svc.list_suggestions(1)[0]["suggestion_id"], "CO-0001",
                     "accept", basis_refs=["x"], signed_by="CO-0001")
        after = self.conn.execute("SELECT COUNT(*) AS n FROM audit_record").fetchone()["n"]
        self.assertEqual(after, before + 1)

    def test_asset_snapshot_may_be_overwritten(self):
        """资产快照是镜像，允许覆盖（方案 §9.1 的唯一业务例外）。"""
        row = self.conn.execute("SELECT asset_pk FROM asset_snapshot LIMIT 1").fetchone()
        self.conn.execute(
            "UPDATE asset_snapshot SET description='覆盖测试' WHERE asset_pk=?",
            (row["asset_pk"],))
        self.conn.commit()
        got = self.asset_by_pk(row["asset_pk"])["description"]
        self.assertEqual(got, "覆盖测试")


class TestHashChain(DbCase):
    def test_chain_verifies_after_writes(self):
        svc = SuggestionService(self.conn)
        biz = self.first_workorder()
        svc.create_suggestion(biz, "CO-0001")
        svc.act(svc.list_suggestions(1)[0]["suggestion_id"], "CO-0001", "accept",
                basis_refs=["人工复核"], signed_by="CO-0001")
        ok, at, detail = audit.verify_chain(self.conn)
        self.assertTrue(ok, detail)
        self.assertIsNone(at)

    def test_tamper_detected(self):
        """篡改任一记录 → 链校验失败（对应停止条件 S4）。"""
        svc = SuggestionService(self.conn)
        biz = self.first_workorder()
        svc.create_suggestion(biz, "CO-0001")
        # 绕过触发器直接改库（模拟外部篡改）
        self.conn.execute("PRAGMA writable_schema=OFF")
        self.conn.execute(
            "DROP TRIGGER IF EXISTS trg_audit_no_update")
        self.conn.execute(
            "UPDATE audit_record SET payload = json_set(payload,'$.result_type','suggest')")
        self.conn.commit()
        ok, at, detail = audit.verify_chain(self.conn)
        self.assertFalse(ok)
        self.assertIsNotNone(at)
        self.assertIn("哈希", detail + " ")

    def test_degraded_flag_on_break(self):
        svc = SuggestionService(self.conn)
        svc.create_suggestion(self.first_workorder(), "CO-0001")
        self.conn.execute("DROP TRIGGER IF EXISTS trg_audit_no_update")
        self.conn.execute("UPDATE audit_record SET actor_role='x'")
        self.conn.commit()
        st = audit.chain_status(self.conn)
        self.assertFalse(st["ok"])
        self.assertTrue(st["read_only_degraded"])

    def test_service_refuses_when_chain_broken(self):
        """链断裂 → 不再产出新建议（P2 的运行时保障，对应 S4）。"""
        svc = SuggestionService(self.conn)
        svc.create_suggestion(self.first_workorder(), "CO-0001")
        self.conn.execute("DROP TRIGGER IF EXISTS trg_audit_no_update")
        self.conn.execute("UPDATE audit_record SET actor_role='x'")
        self.conn.commit()
        with self.assertRaises(RuntimeError):
            svc.create_suggestion(self.first_workorder(), "CO-0001")


class TestSixFields(DbCase):
    def test_suggestion_carries_six_fields(self):
        """f45 六字段：业务编号 / 资料版本 / 事实引用 / 建议结果 / 人工改动 / 签核人。"""
        svc = SuggestionService(self.conn)
        biz = self.first_workorder()
        p = svc.create_suggestion(biz, "CO-0001")
        for k in ("business_no", "source_snapshot_version", "fact_refs",
                  "suggestion_result", "human_amendments", "signed_by"):
            self.assertIn(k, p, f"缺少六字段之一: {k}")
        self.assertTrue(p["fact_refs"])
        self.assertIsNone(p["signed_by"])

    def test_human_amendment_recorded_without_overwriting(self):
        """人工改动以新记录表达，原记录不被覆盖（f45 / f15）。"""
        svc = SuggestionService(self.conn)
        biz = self.first_workorder()
        p = svc.create_suggestion(biz, "CO-0001")
        sid = p["suggestion_id"]
        svc.act(sid, "CO-0001", "amend", after_value={"asset_pk": "PK-XXX"},
                basis_refs=["现场核对"], signed_by="CO-0001")
        sug = svc.get_suggestion(sid)
        self.assertEqual(sug["payload"]["suggestion_result"]["candidates"][0]["asset_pk"],
                         p["suggestion_result"]["candidates"][0]["asset_pk"])
        n = self.conn.execute(
            "SELECT COUNT(*) AS n FROM audit_record WHERE record_type='human_action'"
            " AND suggestion_id=?", (sid,)).fetchone()["n"]
        self.assertEqual(n, 1)


class TestRebuild(DbCase):
    def test_rebuild_maps_to_six_nodes(self):
        svc = SuggestionService(self.conn)
        biz = self.first_workorder()
        p = svc.create_suggestion(biz, "CO-0001")
        sid = p["suggestion_id"]
        svc.act(sid, "CO-0001", "accept", basis_refs=["复核"], signed_by="CO-0001")
        svc.act(sid, "CO-0001", "dispatch", basis_refs=["原系统派单"], signed_by="CO-0001")
        rb = svc.rebuild(biz)
        self.assertIn("资产定位", rb["nodes_covered"])
        self.assertIn("派工", rb["nodes_covered"])
        self.assertGreaterEqual(rb["record_count"], 3)


if __name__ == "__main__":
    unittest.main()
