# 贡献指南

感谢参与。本项目是《公共设施报修与资产协同 · 首期系统设计方案》的可运行实现，
以下约定请在提 PR 前读一遍——它们大多直接来自方案里那些**不可让步的硬约束**。

## 硬约束（改代码前必须先确认没有破坏）

| # | 约束 | 方案出处 | 怎么验 |
|---|---|---|---|
| 1 | 建议服务**不产生任何业务副作用** | §1.3 P1 | `tests/test_core_matching.py::test_no_automatic_dispatch` |
| 2 | 审计记录**只能追加**，不得 UPDATE / DELETE | §1.3 P2 / §9.1 | `tests/test_core_audit.py::TestAppendOnly` |
| 3 | 哈希链公式不得改动 | §9.2 | `tests/test_core_audit.py::TestHashChain` |
| 4 | AI 输出**不得被当作决定**，必须带 `result_type` / `trigger_reason` | §1.3 P3 | `tests/test_core_gates.py` |
| 5 | LLM **只重排与解释，不生成新事实**；越界输出必须丢弃 | §6.6 铁律 | `core/rank.py::llm_rerank` |
| 6 | 歧义记录**一律不进自动推荐样本** | f33 / §5.2.1 | `tests/test_core_gates.py::TestRecallFiltering` |
| 7 | 比例型指标的**分母统一为去重后的业务编号数** | f53 / §12.4 | `tests/test_core_handover.py::TestMetrics` |
| 8 | 制度性未决项**默认取保守值**，不得替业务方预设口径 | §2 末段 / §14.3 | `config/default.yaml` 注释 |

如果你要改这八条里任何一条，**先开 issue 讨论**，不要直接提 PR。

## 环境

```bash
python --version        # 需要 >= 3.10
git clone <repo>
cd asset-suggestion

# 方式一：不装包，直接用 src 布局（推荐用于开发）
export PYTHONPATH=src
python -m asset_suggestion test

# 方式二：装成可编辑包
pip install -e .
asset-suggestion test
```

**运行时零第三方依赖**，不需要 `pip install -r requirements.txt`（那个文件里只有注释）。

## 提交前必跑

```bash
python -m asset_suggestion test                 # 单元测试，必须全绿
python -m asset_suggestion seed --assets 2000 --workorders 60
python -m asset_suggestion replay -n 60          # 回放 + 指标，错误数必须为 0
```

回放的**命中率先不要当结论看**：合成样本只用于链路自检，不是 §12.3 的资产定位准确率。
如果你的改动让 Top-1/Top-3 有变化，请在 PR 里说明原因，并说明是否影响了
「锚定覆盖率」「闸门触发分布」这类**更能反映链路行为**的指标。

## 代码风格

- 中文注释，解释**为什么**，不复述代码在做什么
- 核心逻辑（匹配层五阶段、治理四模块）必须留痕可追溯：判定依据要写进 `reasons` / `fact_refs`
- 新增依赖前先问一句：能不能用标准库？本项目的零依赖是刻意选择，不是偶然
- 目录职责：`core/` 放业务逻辑，`utils/` 放**不依赖项目内其他模块**的纯工具

## PR 流程

1. 从 `main` 切分支：`feat/xxx` / `fix/xxx` / `docs/xxx`
2. 改动带上测试；测试放 `tests/`，文件名与 `src/` 结构对应（`test_<路径>.py`）
3. 提交信息用中文，动词开头，说清**改了什么、为什么**
4. 填 `.github/PULL_REQUEST_TEMPLATE.md` 里的清单
5. CI（`.github/workflows/ci.yml`）全绿再等评审

## 涉及配置改动的额外要求

改 `config/default.yaml` 时：

- 只放**非机密**项。口令、密钥、证书一律不进仓库
- 制度性未决项要么保持留空/保守值，要么在 PR 里写明**依据是什么、谁确认的**
- 改了配置项名，要同步改 `core/config.py` 的映射与 `docs/design.md` 的清单

## 报告问题

- 缺陷与需求分别用 `.github/ISSUE_TEMPLATE/` 里的模板
- 涉及匹配的缺陷，请附上：**原始描述文本 + 业务编号 + 你期望的候选 + 实际候选**
