"""交接提醒：五要件 + 接手人确认闭环。

对应方案 §7 与 f23：
    交接至少包含业务编号、当前版本、处理状态、未解决事项、接手人。
    对方确认接手才结束本岗待办，仅发送消息不算完成。

f51 同样强调：不能用发送通知数量代替实际完成量。
因此闭环判定是**硬判定**，不是建议。

超时升级（f25）：异常先分派实际处理人，超过约定时限再升级到运营主任。
但"约定时限"的具体数值**尚未确认**，所以：
    升级阈值是配置项，默认留空且不启用；
    未配置时只提示"已达交接确认时限"并列出待确认清单，
    **不自动升级、不自动改派**。
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from . import audit
from .config import CONFIG
from .db import jload


def _latest(conn, business_no: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT payload, created_at FROM audit_record"
        " WHERE business_no = ? AND record_type = 'handover' ORDER BY seq DESC LIMIT 1",
        (business_no,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["payload"] = jload(d["payload"], {})
    return d


def create_handover(
    conn,
    business_no: str,
    receiver: Optional[str],
    unresolved_items,
    actor_id: str,
    actor_role: Optional[str] = None,
    current_version: str = "",
    processing_status: str = "",
) -> dict:
    """发起交接。五个要件缺一不可；接手人推导不出时留空并标记。"""
    if actor_role is None:
        from . import authz
        actor_role = authz.role_of(actor_id)
    if receiver is None or not str(receiver).strip():
        receiver = ""
    items = list(unresolved_items or [])
    payload = {
        "business_no": business_no,
        "current_version": current_version,
        "processing_status": processing_status,
        "unresolved_items": items,
        "receiver": receiver,
        "receiver_missing": not receiver,
        "notified_at": audit.now_iso(),
        "receiver_confirmed_at": None,
        "escalation_state": "not_configured",
    }
    head = audit.append(
        conn, "handover", business_no, payload, actor_id=actor_id, actor_role=actor_role
    )
    return {"handover_id": head["record_id"], "notified_at": payload["notified_at"], **payload}


def confirm_handover(conn, business_no: str, actor_id: str,
                     actor_role: Optional[str] = None) -> dict:
    """接手人确认——这一步才算闭环。"""
    if actor_role is None:
        from . import authz
        actor_role = authz.role_of(actor_id)
    last = _latest(conn, business_no)
    if last is None:
        raise ValueError(f"业务 {business_no} 没有交接记录")
    if last["payload"].get("receiver_confirmed_at"):
        raise ValueError("该交接已确认，重复确认被拒绝")
    payload = dict(last["payload"])
    payload["receiver_confirmed_at"] = audit.now_iso()
    payload["confirmed_by"] = actor_id
    head = audit.append(
        conn, "handover", business_no, payload, actor_id=actor_id, actor_role=actor_role
    )
    return {"handover_id": head["record_id"], **payload}


def status(conn, business_no: str) -> dict:
    """闭环判定：notified != NULL 且 confirmed == NULL → 已通知未闭环。"""
    last = _latest(conn, business_no)
    if last is None:
        return {"business_no": business_no, "state": "none", "closed": False}
    p = last["payload"]
    notified = p.get("notified_at")
    confirmed = p.get("receiver_confirmed_at")
    closed = bool(notified and confirmed)
    wait = None
    if notified and confirmed:
        try:
            t0 = _dt.datetime.fromisoformat(notified)
            t1 = _dt.datetime.fromisoformat(confirmed)
            if t0.tzinfo is None:
                t0 = t0.replace(tzinfo=_dt.timezone.utc)
            if t1.tzinfo is None:
                t1 = t1.replace(tzinfo=_dt.timezone.utc)
            wait = round((t1 - t0).total_seconds() / 60.0, 2)
        except (ValueError, TypeError):
            wait = None
    return {
        "business_no": business_no,
        "state": "closed" if closed else ("notified_not_closed" if notified else "none"),
        "closed": closed,
        "notified_at": notified,
        "receiver_confirmed_at": confirmed,
        "receiver": p.get("receiver"),
        "unresolved_items": p.get("unresolved_items", []),
        "wait_minutes": wait,
        "escalation_state": p.get("escalation_state", "not_configured"),
        "escalation_enabled": CONFIG.handover_timeout_minutes is not None,
    }


def pending_escalations(conn, now: Optional[_dt.datetime] = None) -> list:
    """超时未确认清单。

    无论是否配置阈值，这里都只**列出**，绝不自动升级或改派。
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    threshold = CONFIG.handover_timeout_minutes
    out = []
    for row in conn.execute(
        "SELECT business_no, payload, created_at FROM audit_record"
        " WHERE record_type = 'handover' ORDER BY seq"
    ):
        d = dict(row)
        p = jload(d["payload"], {})
        if p.get("receiver_confirmed_at"):
            continue
        notified = p.get("notified_at")
        if not notified:
            continue
        try:
            t0 = _dt.datetime.fromisoformat(notified)
            if t0.tzinfo is None:
                t0 = t0.replace(tzinfo=_dt.timezone.utc)
        except (ValueError, TypeError):
            continue
        age = round((now - t0).total_seconds() / 60.0, 2)
        if threshold is not None and age >= threshold:
            out.append({
                "business_no": d["business_no"], "age_minutes": age,
                "receiver": p.get("receiver"),
                "unresolved_items": p.get("unresolved_items", []),
                "action": "已超过约定时限，可按规则升级至设施运营主任（系统不自动执行）",
            })
        else:
            out.append({
                "business_no": d["business_no"], "age_minutes": age,
                "receiver": p.get("receiver"),
                "unresolved_items": p.get("unresolved_items", []),
                "action": ("已达交接确认时限，等待人工判断" if threshold is None
                           else "未达约定时限"),
                "threshold_configured": threshold is not None,
            })
    return out
