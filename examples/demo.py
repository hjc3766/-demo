#!/usr/bin/env python3
"""可运行的示例：在临时库里造几条工单，产出一条建议单。

不依赖服务、不污染正式数据，跑完自动清理临时库：

    python examples/demo.py

输出是方案 §6.6 的建议单输出规格：
建议编号 / 业务编号 / 资料版本 / 候选 1-3（资产·坐标·依据·待确认）/ 【转人工提示】
"""
from __future__ import annotations

import os
import sys
import tempfile

# 允许在未安装包的情况下直接运行（src/ 布局）
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from asset_suggestion.core.db import session          # noqa: E402
from asset_suggestion.core.seed import seed           # noqa: E402
from asset_suggestion.core.service import SuggestionService, render_card  # noqa: E402


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="asset_suggestion_demo_")
    db_path = os.path.join(tmpdir, "demo.db")

    with session(db_path) as conn:
        stats = seed(conn, assets=60, workorders=8, seed_value=7)
        print("=" * 72)
        print("① 合成数据")
        print("=" * 72)
        for k, v in stats.items():
            print(f"  {k}: {v}")

        svc = SuggestionService(conn)
        rows = conn.execute(
            "SELECT business_no, raw_text FROM hotline_intake ORDER BY business_no LIMIT 8"
        ).fetchall()

        print()
        print("=" * 72)
        print("② 群众原话（阶段一输入）")
        print("=" * 72)
        for r in rows:
            print(f"  {r['business_no']}  {r['raw_text']}")

        print()
        print("=" * 72)
        print("③ 建议单（方案 §6.6 输出规格）")
        print("=" * 72)
        for r in rows[:2]:
            payload = svc.create_suggestion(r["business_no"], actor_id="CO-0001")
            print(render_card(payload))
            print("-" * 72)

    print(f"临时库已丢弃：{db_path}")
    print("提示：真实场景的资产与工单请走阶段 1 盘点后的连接器，不要用合成数据出结论。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
