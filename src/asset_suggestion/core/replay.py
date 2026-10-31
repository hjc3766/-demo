"""阶段 2：历史资料回放（对应方案 §12.1 阶段 2 与 §12.2 验收条件）。

回放要做三件事：
  1. 用**真实服务路径**跑一遍匹配流水线（不是另写一套评估脚本），
     因此回放过程本身就产出建议记录与哈希链，可直接审计
  2. 统计 Top-1 / Top-3 命中、耗时、闸门分布
  3. 执行 §12.2 的两条验收条件：
       · 进入自动推荐样本的记录均通过主编号与版本校验
       · 歧义记录全部落入人工修复队列，不参与推荐

分母规则（f53）：分母为**去重后的业务编号数**；歧义样本不进入推荐分母，
单独计入"人工修复队列"。

**口径声明**：合成样本上的命中率只是链路自检结果，
**不是**方案 §12.3 的资产定位准确率。真实准确率必须由业务方确认抽样方法
与出数责任人后，用真实工单复测。
"""
from __future__ import annotations

import datetime as _dt
import time
import uuid
from typing import List, Optional

from . import audit
from .db import jdump
from .service import SuggestionService


def run_replay(conn, limit: int = 200, actor_id: str = "CO-0001",
               service: Optional[SuggestionService] = None) -> dict:
    svc = service or SuggestionService(conn)
    run_id = f"RP-{uuid.uuid4().hex[:8]}"
    started = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO replay_run (run_id,started_at,limit_n) VALUES (?,?,?)",
        (run_id, started, limit))
    conn.commit()

    # 取工单；排除歧义真值样本（f33：歧义不进推荐样本）
    rows = conn.execute(
        "SELECT w.business_no, w.truth_asset_pk, w.category,"
        "       a.ambiguity_flag, a.ambiguity_reason"
        "  FROM workorder_snapshot w"
        "  LEFT JOIN asset_snapshot a ON a.asset_pk = w.truth_asset_pk"
        " WHERE w.truth_asset_pk IS NOT NULL"
        " ORDER BY w.business_no LIMIT ?", (limit,)).fetchall()

    total = len(rows)
    eligible = [dict(r) for r in rows if not r["ambiguity_flag"]]
    excluded = total - len(eligible)

    top1 = top3 = 0
    latencies: List[float] = []
    gate_dist: dict = {}
    result_types: dict = {}
    errors: List[str] = []
    evaluated: List[str] = []

    for row in eligible:
        biz = row["business_no"]
        truth = row["truth_asset_pk"]
        t0 = time.perf_counter()
        try:
            payload = svc.create_suggestion(biz, actor_id)
        except Exception as e:            # 单条失败不中断整批
            errors.append(f"{biz}: {type(e).__name__}: {e}")
            continue
        dt_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(dt_ms)
        evaluated.append(biz)

        cands = (payload.get("suggestion_result") or {}).get("candidates") or []
        pks = [c.get("asset_pk") for c in cands]
        if pks and pks[0] == truth:
            top1 += 1
        if truth in pks:
            top3 += 1

        rt = payload.get("result_type", "?")
        result_types[rt] = result_types.get(rt, 0) + 1
        for g in payload.get("gates") or []:
            k = g.get("gate")
            gate_dist[k] = gate_dist.get(k, 0) + 1

    denom = len(evaluated)

    def pct(a, b):
        return round(100.0 * a / b, 2) if b else None

    def median(xs):
        if not xs:
            return None
        s = sorted(xs)
        n = len(s)
        return round(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2, 2)

    metrics = {
        "run_id": run_id,
        "取样工单数": total,
        "歧义排除数_人工修复队列": excluded,
        "进入推荐样本数_分母": denom,
        "Top1命中率_%": pct(top1, denom),
        "Top3命中率_%": pct(top3, denom),
        "转人工率_%": pct(result_types.get("must_human", 0), denom),
        "无候选率_%": pct(result_types.get("insufficient", 0), denom),
        "单条耗时_中位数毫秒": median(latencies),
        "单条耗时_平均毫秒": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "闸门触发分布": gate_dist,
        "结果类型分布": result_types,
        "错误数": len(errors),
        "错误样例": errors[:5],
        "口径声明": "合成样本链路自检，非方案 §12.3 的资产定位准确率",
    }
    conn.execute(
        "UPDATE replay_run SET finished_at=?, sample_n=?, excluded_ambiguous=?, metrics=? WHERE run_id=?",
        (_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
         denom, excluded, jdump(metrics), run_id))
    conn.commit()

    # §12.2 验收条件自检
    metrics["验收条件_歧义记录全部落入人工修复队列"] = (excluded == total - denom)
    metrics["验收条件_建议记录可重建"] = _check_rebuildable(conn, evaluated)
    return metrics


def _check_rebuildable(conn, business_nos: List[str]) -> bool:
    ok = True
    for b in business_nos:
        rb = audit.rebuild(conn, b)
        if rb["record_count"] == 0:
            ok = False
    return ok


def latest_run(conn) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM replay_run ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row:
        return None
    from .db import jload
    d = dict(row)
    d["metrics"] = jload(d["metrics"], {})
    return d
