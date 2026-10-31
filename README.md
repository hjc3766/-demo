# asset-suggestion · 建议服务 MVP

> **公共设施报修与资产协同 · 首期系统设计方案的可运行实现。**
>
> 把群众/巡查人员的原话（地标描述）转成 Top-3 资产候选 + 待确认字段 + 事实依据 + 转人工提示，
> **全程不产生任何业务副作用**；所有产出写进 append-only 审计链，可按业务编号重建。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![依赖](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)

---

## 这是什么

一个**只读建议服务**。它不接管工单，不自动派单，只做一件事：

```
群众原话 ──▶ 归一化 ──▶ 行政锚定 ──▶ 候选召回 ──▶ 排序解释 ──▶ 闸门判定 ──▶ 建议单
                                                                            │
                                              append-only 审计链 ◀───────────┘
```

三条架构承诺（方案 §1.3）在代码里是**可执行检验**，不是口号：

| 承诺 | 怎么检验 |
|---|---|
| **P1** 永不产生业务副作用 | 代码里没有写向业务系统的连接器；业务系统三表只读镜像 |
| **P2** 永不覆盖历史 | 审计表在**数据库层**用触发器禁 UPDATE/DELETE + 全局哈希链 |
| **P3** AI 输出永不被当作决定 | 每条建议带 `result_type` / `trigger_reason`；派单由人在原系统确认 |

> 合成样本上的命中率只是**链路自检**，不是方案 §12.3 的"资产定位准确率 98%"——
> 那项的抽样口径与出数责任人尚未确认，系统因此**不产出任何对外达标结论**。

---

## 怎么装

**运行时零第三方依赖**，只用 Python 标准库（≥ 3.10）。

```bash
git clone <repo>
cd asset-suggestion

# 方式一：开发模式，不装包
export PYTHONPATH=src          # Windows: $env:PYTHONPATH="src"

# 方式二：装成可编辑包（推荐装完就能用 asset-suggestion 命令）
pip install -e .
```

> `requirements.txt` 里只有注释——这不是漏写，是刻意的选择：交付对象是政务专网里的
> 可私有化闭源系统，依赖越少，部署环境要准备的东西越少。

---

## 怎么跑

```bash
# ① 生成云州模拟数据（默认 2000 资产 + 300 工单）
python -m asset_suggestion seed --assets 2000 --workorders 60

# ② 跑单元测试（69 项）
python -m asset_suggestion test

# ③ 跑阶段 2 回放 + 过程指标
python -m asset_suggestion replay -n 60

# ④ 起服务，浏览器打开 http://127.0.0.1:8762/
python -m asset_suggestion serve --port 8762
```

Windows PowerShell 下记得先设编码，否则中文输出会花：

```powershell
$env:PYTHONIOENCODING='utf-8'
```

### 三分钟看效果

```bash
python examples/demo.py
```

在临时库里造 8 条工单，产出一条建议单并打印出来——不用起服务、不污染正式数据。

### 前端长什么样

单页**零外部依赖**界面（可离线），六个区块：生成建议 / 建议记录列表 / 建议单 + 候选分布图 + 建议单原文 /
人工动作 + 交接提醒 + 过程指标 / 链路重建。

- 左上角下拉切换**岗位账号**，切换后建议单字段按岗位资料范围被**服务端裁剪**
  （社区服务代表看不到坐标、别名、描述、迁移记录等）
- 「候选分布示意图」是内联 SVG：画锚定单元边界、锚点、Top-3 候选
  （生产环境换成 Leaflet / MapLibre，方案 §4.1）

---

## 目录导览

```
asset-suggestion/
├── README.md              本文件：门面
├── LICENSE                MIT
├── CHANGELOG.md           版本更新记录
├── CONTRIBUTING.md        贡献指南（含 8 条不可让步的硬约束）
├── requirements.txt       依赖清单（零运行时依赖）
├── pyproject.toml         打包与入口声明
├── .gitignore
│
├── src/asset_suggestion/  源代码主目录
│   ├── main.py            程序入口（命令行）
│   ├── core/              核心业务逻辑
│   └── utils/             通用小工具（不依赖项目内其他模块）
│
├── docs/
│   ├── design.md          设计说明：方案↔实现映射、差异清单、待确认项
│   └── api.md             接口说明：HTTP API / CLI / Python API
│
├── tests/                 测试代码（文件名与 src 结构对应）
├── examples/demo.py       跑得起来的示例
├── scripts/               构建、部署脚本
├── config/default.yaml    非机密默认配置（制度性未决项都放这儿）
├── data/                  样例数据 + 运行时 SQLite（已 gitignore）
└── .github/               CI、Issue 与 PR 模板
```

---

## 快速验证

| 想验什么 | 怎么验 |
|---|---|
| 链路能跑通、闸门该触发时触发 | `python -m asset_suggestion replay -n 60`，看「错误数: 0」与验收条件是否 true |
| 建议永不覆盖历史 | `python -m asset_suggestion test`，含「UPDATE 被拒」「篡改即断链」「断链后停发建议」 |
| 越权字段不下发 | `tests/smoke_e2e.py`：同一候选换个岗位账号，坐标/别名/迁移记录消失 |
| 六节点链路可重建 | 界面上点「重建本单」，或 `GET /api/audit/{business_no}` |
| 端到端闭环 | `python tests/smoke_e2e.py http://127.0.0.1:8762`（21 项断言） |

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [`docs/design.md`](docs/design.md) | 方案↔实现逐条映射、关键实现细节、**与方案的差异清单（已知边界）**、制度性未决项清单、合成数据构成 |
| [`docs/api.md`](docs/api.md) | HTTP API（19 端点）、命令行、Python API、数据表与写权限 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 贡献流程与**8 条不可让步的硬约束**（改之前先读） |
| [`CHANGELOG.md`](CHANGELOG.md) | 版本记录 |

权威规格始终是《公共设施报修与资产协同 · 首期系统设计方案》V1.0，
本仓库只是它的实现，**两者冲突时以方案为准**。

---

## 配置

非机密默认配置在 [`config/default.yaml`](config/default.yaml)，覆盖下列内容：

- 运行参数（库路径、演示规模、同步周期）
- **制度性未决项**（对应方案 §14.3 的 12 项）——默认一律取保守值：
  留存 90 天 / 自动删除关 / 双人确认关 / 交接约定时限留空 / 抽样方法与出数责任人留空
- 匹配层参数（召回半径、通道条数、Top-N、置信分档 δ）
- 本地模型服务地址（留空则自动降级为规则基线，业务不中断）
- 各岗位可查看的五类资料范围

口令、密钥、证书**一律不进仓库**。

---

## 许可

[MIT](LICENSE)。方案 §4.1 选型的第三方组件均为宽松许可（PostgreSQL/PostGIS、WTFPL、MIT、
Apache-2.0、BSD），可私有化闭源交付；`AGPL` 与 `NOASSERTION` 许可的组件**未进入本交付物**。
