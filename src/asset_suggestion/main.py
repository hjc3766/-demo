"""程序入口（命令行）。

用法：
    python -m asset_suggestion seed            # 生成模拟数据
    python -m asset_suggestion serve           # 启动 HTTP 服务
    python -m asset_suggestion replay -n 200   # 历史回放 + 指标
    python -m asset_suggestion demo            # 单条建议演示（命令行出建议单）
    python -m asset_suggestion test            # 跑单元测试

src/ 布局下若未安装本包，先把 src 放进 PYTHONPATH：
    export PYTHONPATH=src        # Linux/macOS
    $env:PYTHONPATH="src"        # Windows PowerShell
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .core.config import CONFIG
from .core.db import init_db, session
from .core.service import SuggestionService, render_card
from .utils import paths


def _project_root():
    """项目根目录；装包后返回 None（此时一切相对当前工作目录）。"""
    return paths.project_root()


def cmd_seed(args):
    from .core.seed import seed
    with session(args.db) as conn:
        stats = seed(conn, assets=args.assets, workorders=args.workorders,
                     degraded=args.degraded)
    print("模拟数据已生成：")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"数据库：{os.path.abspath(args.db)}")


def cmd_serve(args):
    from .core import api
    with session(args.db) as conn:
        api.serve(conn, host=args.host, port=args.port)


def cmd_replay(args):
    from .core import replay as rp
    with session(args.db) as conn:
        m = rp.run_replay(conn, limit=args.n)
    print("回放结果：")
    print(json.dumps(m, ensure_ascii=False, indent=2))


def cmd_demo(args):
    with session(args.db) as conn:
        svc = SuggestionService(conn)
        if args.business_no:
            biz = args.business_no
        else:
            row = conn.execute(
                "SELECT business_no FROM workorder_snapshot ORDER BY business_no LIMIT 1").fetchone()
            if not row:
                print("没有工单样本，请先执行 seed")
                return
            biz = row["business_no"]
        payload = svc.create_suggestion(biz, args.actor_id)
        print(render_card(payload))


def cmd_test(args):
    import unittest
    root = _project_root()
    if root is None:
        print("未找到项目根目录（tests/ 不在包外），请在源码仓库中运行本命令。")
        sys.exit(2)
    tests_dir = str(root / "tests")
    src_dir = str(root / "src")
    for p in (src_dir, tests_dir):
        if p not in sys.path:
            sys.path.insert(0, p)
    loader = unittest.TestLoader()
    suite = loader.discover(tests_dir, top_level_dir=tests_dir)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


def build_parser():
    p = argparse.ArgumentParser(
        prog="asset_suggestion",
        description="公共设施报修与资产协同 · 建议服务 MVP",
    )
    p.add_argument("--db", default=CONFIG.db_path, help="SQLite 路径")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="生成模拟数据")
    s.add_argument("--assets", type=int, default=None)
    s.add_argument("--workorders", type=int, default=None)
    s.add_argument("--degraded", action="store_true", help="模拟连接器中断（G4）")
    s.set_defaults(func=cmd_seed)

    s = sub.add_parser("serve", help="启动 HTTP 服务")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8760)
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("replay", help="历史回放")
    s.add_argument("-n", type=int, default=200)
    s.set_defaults(func=cmd_replay)

    s = sub.add_parser("demo", help="生成一条建议单")
    s.add_argument("--business-no", default=None)
    s.add_argument("--actor-id", default="CO-0001")
    s.set_defaults(func=cmd_demo)

    s = sub.add_parser("test", help="运行单元测试")
    s.set_defaults(func=cmd_test)
    return p


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    root = _project_root()
    if root is not None:
        os.chdir(str(root))
    args.func(args)


if __name__ == "__main__":
    main()
