"""降级与可用性（对应方案 §10 与 f34/f35）。

f34：每 15 分钟更新已授权的业务状态；原始资料按版本更新；
      信息超过一个更新周期就标为待刷新，不能作为实时承诺。
f35：接口中断时继续使用带时间戳的上一份只读快照，并显示"暂未更新"；
      重连后按业务编号去重补齐，不反复触发同一待办；现有人工流程继续运行。

本模块只负责**状态判定与展示口径**，不做任何自动修复或拦截：
  * stale_flag  → 资产快照过期，界面显示"待刷新"
  * 连接器失效 → 走快照降级，命中 G4 闸门，界面显示"暂未更新（快照时间 X）"
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Optional

from .config import CONFIG
from .db import jload

CONNECTORS = ["GIS资产库", "维修工单", "社区热线"]


def now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def heartbeat(conn, connector: str, at: Optional[str] = None) -> None:
    ts = at or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO connector_state (connector, last_heartbeat, status) VALUES (?,?,'up')"
        " ON CONFLICT(connector) DO UPDATE SET last_heartbeat=excluded.last_heartbeat,"
        " status='up'", (connector, ts))
    conn.commit()


def connector_status(conn) -> List[dict]:
    period = CONFIG.sync_period_minutes
    cur = now()
    rows = {r["connector"]: dict(r) for r in conn.execute("SELECT * FROM connector_state")}
    out = []
    for name in CONNECTORS:
        d = rows.get(name)
        hb = d.get("last_heartbeat") if d else None
        age = None
        healthy = False
        if hb:
            try:
                t = _dt.datetime.fromisoformat(hb)
                if t.tzinfo is None:
                    t = t.replace(tzinfo=_dt.timezone.utc)
                age = round((cur - t).total_seconds() / 60.0, 2)
                healthy = age < period
            except (ValueError, TypeError):
                age = None
        out.append({
            "connector": name,
            "last_heartbeat": hb,
            "age_minutes": age,
            "healthy": healthy,
            "status": (d.get("status") if d else "unknown"),
            "banner": None if healthy else f"暂未更新（快照时间 {hb or '无'}）",
        })
    return out


def refresh_stale_flags(conn, period_minutes: Optional[int] = None) -> int:
    """按刷新周期标记过期资产（f34）。"""
    period = period_minutes or CONFIG.sync_period_minutes
    cur = now()
    n = 0
    for row in conn.execute("SELECT asset_pk, fetched_at FROM asset_snapshot"):
        d = dict(row)
        fb = d.get("fetched_at")
        stale = True
        if fb:
            try:
                t = _dt.datetime.fromisoformat(fb)
                if t.tzinfo is None:
                    t = t.replace(tzinfo=_dt.timezone.utc)
                stale = (cur - t).total_seconds() / 60.0 >= period
            except (ValueError, TypeError):
                stale = True
        conn.execute("UPDATE asset_snapshot SET stale_flag = ? WHERE asset_pk = ?",
                     (1 if stale else 0, d["asset_pk"]))
        n += 1
    conn.commit()
    return n


def system_banner(conn) -> Optional[str]:
    """全局提示：任一连接器不可用则整站显示"暂未更新"。"""
    for c in connector_status(conn):
        if not c["healthy"]:
            return f"暂未更新：{c['connector']} 快照时间 {c['last_heartbeat'] or '无'}（其余流程照常，人工流程不受影响）"
    return None


def snapshot_info(conn) -> dict:
    """快照信息：供界面显示"最后同步时间"（f13 硬约束）。"""
    row = conn.execute(
        "SELECT MAX(fetched_at) AS last FROM asset_snapshot"
    ).fetchone()
    last = row["last"] if row else None
    return {"last_sync_at": last, "sync_period_minutes": CONFIG.sync_period_minutes}


def apply_version(conn, asset_pk: str, source_version: str, lng: float, lat: float,
                  coord_sys: str, fetched_at: Optional[str] = None) -> bool:
    """记录一个资产版本快照。幂等键 = asset_pk + source_version（f35 重连去重）。

    返回 True 表示写入新版本；False 表示该版本已存在（未重复写入）。
    """
    ts = fetched_at or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    try:
        conn.execute(
            "INSERT INTO asset_version_snapshot (asset_pk, source_version, lng, lat, coord_sys, fetched_at)"
            " VALUES (?,?,?,?,?,?)",
            (asset_pk, source_version, lng, lat, coord_sys, ts))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
