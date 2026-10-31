"""交接闭环与授权裁剪的测试。

对应方案 §7（交接）、§8.3（授权）、f23、f41、f51。
"""
from __future__ import annotations

import unittest

from asset_suggestion.core import authz, handover
from asset_suggestion.core.service import SuggestionService

from _base import DbCase


class TestHandover(DbCase):
    def test_five_elements_present(self):
        """f23 五要件：业务编号 / 当前版本 / 处理状态 / 未解决事项 / 接手人。"""
        biz = self.first_workorder()
        r = handover.create_handover(self.conn, biz, "维修二组-张工",
                                     ["灯杆编号与台账是否一致"],
                                     "CO-0001", current_version="v1-abc",
                                     processing_status="待接手")
        for k in ("business_no", "current_version", "processing_status",
                  "unresolved_items", "receiver"):
            self.assertIn(k, r)
        self.assertTrue(r["unresolved_items"])

    def test_notified_is_not_closed(self):
        """仅发送消息不算完成。"""
        biz = self.first_workorder()
        handover.create_handover(self.conn, biz, "张工", ["待确认项"], "CO-0001")
        st = handover.status(self.conn, biz)
        self.assertEqual(st["state"], "notified_not_closed")
        self.assertFalse(st["closed"])

    def test_confirm_closes(self):
        biz = self.first_workorder()
        handover.create_handover(self.conn, biz, "张工", ["待确认项"], "CO-0001")
        handover.confirm_handover(self.conn, biz, "CO-0001", "维修协调员")
        st = handover.status(self.conn, biz)
        self.assertEqual(st["state"], "closed")
        self.assertTrue(st["closed"])
        self.assertIsNotNone(st["wait_minutes"])

    def test_double_confirm_rejected(self):
        biz = self.first_workorder()
        handover.create_handover(self.conn, biz, "张工", ["x"], "CO-0001")
        handover.confirm_handover(self.conn, biz, "CO-0001", "维修协调员")
        with self.assertRaises(ValueError):
            handover.confirm_handover(self.conn, biz, "CO-0001", "维修协调员")

    def test_missing_receiver_flagged_not_defaulted(self):
        """接手人推导不出时留空并标记，不得默认指派（方案 §7.1）。"""
        biz = self.first_workorder()
        r = handover.create_handover(self.conn, biz, None, ["x"], "CO-0001")
        self.assertEqual(r["receiver"], "")
        self.assertTrue(r["receiver_missing"])

    def test_escalation_not_automatic_when_unconfigured(self):
        """约定时限未确认 → 只提示，不自动升级、不自动改派（f25）。"""
        biz = self.first_workorder()
        handover.create_handover(self.conn, biz, "张工", ["x"], "CO-0001")
        pending = handover.pending_escalations(self.conn)
        mine = [p for p in pending if p["business_no"] == biz]
        self.assertTrue(mine)
        self.assertFalse(mine[0].get("threshold_configured", False))
        self.assertIn("等待人工判断", mine[0]["action"])
        self.assertNotIn("升级至设施运营主任", mine[0]["action"])
        # 交接状态未被系统改动
        st = handover.status(self.conn, biz)
        self.assertEqual(st["escalation_state"], "not_configured")
        self.assertFalse(st["closed"])


class TestAuthz(DbCase):
    def test_role_from_actor_id(self):
        self.assertEqual(authz.role_of("CO-0001"), "维修协调员")
        self.assertEqual(authz.role_of("OP-0007"), "巡查工作人员")
        with self.assertRaises(authz.AuthorizationError):
            authz.role_of("XX-0001")

    def test_server_side_trimming(self):
        """f41：越权字段在服务端裁剪，不依赖前端隐藏。"""
        cands = [{
            "asset_pk": "PK-1", "lng": 120.1, "lat": 30.2, "location_anchor": "YZ1",
            "alias_text": ["大厅"], "description": "d", "history_count": 3,
            "migration_version": "mv-1", "prev_lng": 120.0, "prev_lat": 30.1,
            "prev_asset_code": "WG-1", "source_version": "sv-1",
            "reasons": [{"ref": "coord:PK-1", "detail": "距离"}, {"ref": "history:PK-1", "detail": "历史"}],
        }]
        trimmed = authz.trim_candidates(cands, "社区服务代表")
        self.assertNotIn("lng", trimmed[0])        # 无设施地图权限
        self.assertNotIn("migration_version", trimmed[0])
        self.assertIn("history_count", trimmed[0])  # 有历史维修权限
        full = authz.trim_candidates(cands, "维修协调员")
        self.assertIn("lng", full[0])
        self.assertIn("migration_version", full[0])

    def test_action_permission(self):
        self.assertTrue(authz.can("dispatch", "维修协调员"))
        self.assertFalse(authz.can("dispatch", "巡查工作人员"))
        self.assertFalse(authz.can("accept", "社区服务代表"))
        with self.assertRaises(authz.AuthorizationError):
            authz.require("dispatch", "巡查工作人员")

    def test_service_enforces_permission(self):
        svc = SuggestionService(self.conn)
        p = svc.create_suggestion(self.first_workorder(), "CO-0001")
        with self.assertRaises(authz.AuthorizationError):
            svc.act(p["suggestion_id"], "OP-0001", "dispatch",
                    basis_refs=["x"], signed_by="OP-0001")


class TestMetrics(BaseCase := DbCase):
    def test_denominator_is_deduplicated_business_no(self):
        """f53：分母统一为去重后的业务编号数，不把同一业务拆成多次成功。"""
        from asset_suggestion.core.metrics import process_metrics
        svc = SuggestionService(self.conn)
        biz = self.first_workorder()
        svc.create_suggestion(biz, "CO-0001")
        svc.create_suggestion(biz, "CO-0001")     # 同一业务多次生成
        svc.act(svc.list_suggestions(1)[0]["suggestion_id"], "CO-0001", "accept",
                basis_refs=["x"], signed_by="CO-0001")
        m = process_metrics(self.conn)
        self.assertEqual(m["分母_去重业务编号数"], 1)
        self.assertEqual(m["建议记录数"], 2)

    def test_handover_metrics_present(self):
        from asset_suggestion.core.metrics import process_metrics
        biz = self.first_workorder()
        handover.create_handover(self.conn, biz, "张工", ["x"], "CO-0001")
        m = process_metrics(self.conn)
        self.assertEqual(m["交接发起数"], 1)
        self.assertEqual(m["交接已闭环数"], 0)
        self.assertEqual(m["交接确认率_%"], 0.0)


if __name__ == "__main__":
    unittest.main()
