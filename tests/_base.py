"""测试基座：每个用例一个独立的临时库。"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from asset_suggestion.core.db import init_db, session
from asset_suggestion.core.seed import seed


class DbCase(unittest.TestCase):
    """带模拟数据的临时库。"""

    assets = 120
    workorders = 20
    degraded = False

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sugg_mvp_test_")
        self.db = os.path.join(self.tmp, "t.db")
        self._ctx = session(self.db)
        self.conn = self._ctx.__enter__()
        seed(self.conn, assets=self.assets, workorders=self.workorders,
             degraded=self.degraded, seed_value=42)

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- 便捷查询 --

    def first_workorder(self):
        r = self.conn.execute(
            "SELECT business_no FROM workorder_snapshot ORDER BY business_no LIMIT 1").fetchone()
        return r["business_no"]

    def asset_by_pk(self, pk):
        return self.conn.execute(
            "SELECT * FROM asset_snapshot WHERE asset_pk = ?", (pk,)).fetchone()

    def workorder_for(self, asset_pk: str) -> str:
        """返回指向该资产的工单；若没有则造一条（保证用例确定性，不 skip）。

        描述用该资产自己的地标别名构造，确保工单确实指向它——
        否则闸门用例会因为"资产没进 Top-K 候选"而假阴性。
        """
        r = self.conn.execute(
            "SELECT business_no FROM workorder_snapshot WHERE truth_asset_pk=? LIMIT 1",
            (asset_pk,)).fetchone()
        if r:
            return r["business_no"]
        asset = self.asset_by_pk(asset_pk)
        aliases = json.loads(asset["alias_text"] or "[]")
        alias = aliases[0] if aliases else f"{asset['asset_category']}"
        biz = f"WO-TEST-{str(asset_pk)[-6:]}"
        self.conn.execute(
            "INSERT OR REPLACE INTO hotline_intake (intake_id,business_no,raw_text,photos,received_at)"
            " VALUES (?,?,?,'[]',?)",
            (f"IN-TEST-{str(asset_pk)[-6:]}", biz, f"{alias}不亮了",
             "2026-10-29T00:00:00+00:00"))
        self.conn.execute(
            "INSERT OR REPLACE INTO workorder_snapshot (business_no,status,category,assignee,dept,"
            "updated_at,community_done_at,repair_done_at,ledger_done_at,truth_asset_pk)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (biz, "待派工", asset["asset_category"], None, "维修一组",
             "2026-10-29T00:00:00+00:00", None, None, None, asset_pk))
        self.conn.commit()
        return biz

    def set_connector_down(self, connector="GIS资产库", minutes=40):
        import datetime as dt
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)).isoformat(
            timespec="seconds")
        self.conn.execute(
            "UPDATE connector_state SET last_heartbeat=?, status='down' WHERE connector=?",
            (old, connector))
        self.conn.commit()
