"""过程指标计算（对应方案 §12.4）。

分母规则（f53）：所有比例型指标的**分母统一为去重后的业务编号数**；
三岗完成时间分别记录；**不得把同一业务拆成多次"成功"**。

本模块只统计已落库的事实（建议记录、人工动作、交接记录），
不生成任何"达标结论"——方案 §12.3 的两项验收指标在抽样方法与出数责任人
被业务方确认之前，不得对外宣称达标（配置项默认留空）。
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List

from . import audit, handover
from .db import jload

GATE_KEYS = ("G1_迁移编号未变坐标已变", "G2_资料缺少依据",
             "G3_两个版本冲突", "G4_关键系统不可用")


def _suggestion_payloads(conn) -> List[dict]:
    out = []
    for r in conn.execute("SELECT payload FROM audit_record WHERE record_type='suggestion' ORDER BY seq"):
        d = dict(r)
        out.append(jload(d["payload"], {}))
    return out


def _distinct_business(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(DISTINCT business_no) AS n FROM audit_record").fetchone()
    return row["n"] if row else 0


def process_metrics(conn) -> dict:
    payloads = _suggestion_payloads(conn)
    denom = _distinct_business(conn)

    gate_dist = {k: 0 for k in GATE_KEYS}
    result_types = {"suggest": 0, "must_human": 0, "insufficient": 0}
    bands = {"high": 0, "mid": 0, "low": 0}
    for p in payloads:
        rt = p.get("result_type", "suggest")
        result_types[rt] = result_types.get(rt, 0) + 1
        band = (p.get("suggestion_result") or {}).get("confidence_band")
        if band in bands:
            bands[band] += 1
        for g in p.get("gates") or []:
            if g.get("gate") in gate_dist:
                gate_dist[g["gate"]] += 1

    # 人工改动率：被人工动作修改过的建议数 / 建议数
    amended = conn.execute(
        "SELECT COUNT(DISTINCT suggestion_id) AS n FROM audit_record"
        " WHERE record_type='human_action' AND suggestion_id IS NOT NULL").fetchone()["n"]
    sug_total = len(payloads)

    # 交接：确认率 / 等待时长
    biz = [r["business_no"] for r in conn.execute(
        "SELECT DISTINCT business_no FROM audit_record WHERE record_type='handover'")]
    closed, waits, contacts = 0, [], []
    for b in biz:
        st = handover.status(conn, b)
        if st["closed"]:
            closed += 1
            if st["wait_minutes"] is not None:
                waits.append(st["wait_minutes"])
        n_contact = 0
        for r in conn.execute(
                "SELECT payload FROM audit_record WHERE business_no=? AND record_type='human_action'",
                (b,)):
            if (jload(r["payload"], {}) or {}).get("action") == "contact":
                n_contact += 1
        contacts.append(n_contact)

    # 可重建率：能按业务编号重建六节点链路的业务占比
    rebuildable = 0
    all_biz = [r["business_no"] for r in conn.execute(
        "SELECT DISTINCT business_no FROM audit_record")]
    for b in all_biz:
        rb = audit.rebuild(conn, b)
        if rb["record_count"] > 0 and rb["nodes_covered"]:
            rebuildable += 1

    chain = audit.chain_status(conn)

    def pct(a, b):
        return round(100.0 * a / b, 2) if b else None

    def median(xs):
        if not xs:
            return None
        s = sorted(xs)
        n = len(s)
        return round(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2, 2)

    def p90(xs):
        if not xs:
            return None
        s = sorted(xs)
        idx = min(len(s) - 1, int(round(0.9 * (len(s) - 1))))
        return round(s[idx], 2)

    return {
        "分母_去重业务编号数": denom,
        "建议记录数": sug_total,
        "结果类型分布": result_types,
        "置信分档分布": bands,
        "闸门触发分布": gate_dist,
        "人工改动率_%": pct(amended, sug_total),
        "交接发起数": len(biz),
        "交接已闭环数": closed,
        "交接确认率_%": pct(closed, len(biz)),
        "交接等待时长_中位数分钟": median(waits),
        "交接等待时长_P90分钟": p90(waits),
        "重新联系次数_均值": round(sum(contacts) / len(contacts), 2) if contacts else None,
        "可重建率_%": pct(rebuildable, len(all_biz)),
        "哈希链校验通过": chain["ok"],
        "哈希链状态": chain["detail"],
        "只读降级中": chain["read_only_degraded"],
    }
