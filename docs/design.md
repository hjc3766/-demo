# 设计说明 —— 建议服务 MVP

> 对应《公共设施报修与资产协同 · 首期系统设计方案》V1.0（`公共设施报修与资产协同-首期系统设计方案.md`）。
> 本文是**实现侧**的说明：方案每一章落到哪个模块、怎么验证、哪里做了取舍。

---

## 1. 这个服务做什么、不做什么

**做**：把群众/巡查人员的原话（地标描述）转成 Top-3 资产候选 + 待确认字段 + 事实依据 + 转人工提示。

**不做**：不接管业务系统，不自动派单，不产生任何业务副作用。

方案 §1.3 的三条架构承诺在实现里都是可执行检验，不是口号：

| 承诺 | 含义 | 检验方式 |
|---|---|---|
| **P1** | 建议服务永不产生业务副作用 | 代码中没有任何写向业务系统的连接器；业务系统三表只读镜像。测试：`tests/test_core_matching.py::test_no_automatic_dispatch` |
| **P2** | 建议永不覆盖历史 | 审计表在**数据库层**用触发器禁 UPDATE/DELETE；全局哈希链。测试：`tests/test_core_audit.py::TestAppendOnly` / `TestHashChain` |
| **P3** | AI 输出永不被当作决定 | 每条建议带 `result_type` / `trigger_reason` / `gates`；派单由授权岗位在原系统确认。测试：`tests/test_core_gates.py` |

---

## 2. 目录与分层

```
src/asset_suggestion/
├── main.py        程序入口（命令行）
├── cli → main     （旧名已并入 main.py）
├── core/          核心业务逻辑（只依赖 utils/ 与标准库）
└── utils/         通用小工具（**不依赖项目内任何其他模块**）
```

分层规则：`core/` 可以引用 `utils/`；`utils/` 绝不反向引用 `core/`。
这条规则让 utils 层可以被单独测、单独复用，也让依赖方向一眼可见。

| 层 | 模块 | 职责 | 对应方案 |
|---|---|---|---|
| utils | `jsonutil.py` | canonical JSON（哈希链基石） | §9.2 |
| utils | `textutil.py` | 字符 bigram 相似度（pgvector 的 MVP 近似） | §4.1 / §6.5 |
| utils | `geo.py` | haversine / 点在多边形 / bbox / 中心点（PostGIS 等价语义） | §6.4 / §6.5 |
| utils | `yamllite.py` | 零依赖 YAML 子集解析（供 `config/default.yaml`） | §2 末段 |
| utils | `paths.py` | 项目根 / 资源路径 / 默认库路径解析 | 工程约定 |
| core | `config.py` | 全部制度性未决项 → 可配置项，默认保守 | §2 / §14.3 |
| core | `db.py` + `schema.sql` | 表结构、审计触发器、三个视图 | §5 / §9.1 |
| core | `districts.py` | 云州区划多边形+别名、资产类别词典、待确认字段模板 | §5.2.1 |
| core | `extract.py` | 阶段一 归一化（规则基线 + LLM 补充） | §6.3 |
| core | `anchor.py` | 阶段二 行政锚定 | §6.4 |
| core | `recall.py` | 阶段三 召回（空间 + 语义两通道并集） | §6.5 |
| core | `rank.py` | 阶段四 排序解释 + 受约束的 LLM 重排 | §6.6 |
| core | `gates.py` | 阶段五 G1–G4 + 接手人规则 | §6.7 |
| core | `audit.py` | append-only + 哈希链 + 校验 + 六节点重建 | §9 |
| core | `authz.py` | 岗位×资料范围授权、字段裁剪、动作权限 | §8 |
| core | `handover.py` | 交接五要件、闭环判定、超时只提示 | §7 |
| core | `sync.py` | 心跳、stale_flag、快照降级、幂等补齐 | §10 |
| core | `service.py` | 编排 + `render_card()`（§6.6 输出规格） | §3.2 / §6.6 |
| core | `metrics.py` / `replay.py` | 过程指标与阶段 2 回放 | §12.2 / §12.4 |
| core | `seed.py` | 云州合成数据（五档难度） | §12.1 |
| core | `api.py` | 标准库 HTTP API | 工程实现 |
| core | `llm.py` | 本地模型服务适配器（无服务即降级） | §4.1 |

---

## 3. 设计 ↔ 实现映射表

**方案章节 → 代码位置 → 验证方式**。

| 方案章节 | 设计要求 | 实现位置 | 验证方式 |
|---|---|---|---|
| §1.3 P1 | 不产生业务副作用 | `core/service.py`、`core/schema.sql` | `test_no_automatic_dispatch` |
| §1.3 P2 | 永不覆盖历史 | `core/schema.sql` 触发器、`core/audit.py` | `test_update_denied` / `test_delete_denied` / `test_tamper_detected` |
| §1.3 P3 | AI 输出不被当作决定 | `core/service.py` 的 `result_type`/`trigger_reason`/`gates` | `tests/test_core_gates.py` |
| §4.1 选型总表 | PostGIS / pgvector / vLLM / Leaflet | 全部做成**可替换适配器**（见第 8 节） | — |
| §5.2.1 | `asset_snapshot` 只读镜像 + 自建别名 | `core/schema.sql`、`core/seed.py` | `test_ambiguous_assets_not_recommended` |
| §5.2.1 歧义判定 | 重复编号 / 无版本迁移 / 坐标系不明 | `core/seed.py` 派生、`core/recall.py` 过滤 | `test_ambiguous_assets_not_recommended`、`test_replay_excludes_ambiguous_truth` |
| §5.2.2 | `suggestion` append-only + 六字段 | `audit_record` + `v_suggestion` 视图 | `test_suggestion_carries_six_fields` |
| §5.2.2 | `result_type` 三值 | `core/service.py` | `test_confidence_band_values` 等 |
| §5.2.3 | `human_action` append-only + 成对留痕 | `core/audit.py::append`、`v_human_action` 视图 | `test_human_amendment_recorded_without_overwriting` |
| §5.2.4 | `handover_record` 状态推进写新行 | `core/handover.py`、`v_handover` 视图 | `test_notified_is_not_closed`、`test_confirm_closes` |
| §6.3 | 阶段一只做要素抽取，不猜资产 | `core/extract.py` | `test_extracts_elements`、`test_extracts_floor_and_building` |
| §6.4 | 阶段二 行政锚定 | `core/anchor.py`、`core/districts.py` | `test_anchor_picks_deepest_match`、`test_anchor_constrains_recall` |
| §6.5 | 阶段三 空间 + 语义两通道并集 | `core/recall.py::SpatialIndex.query` | `test_anchor_constrains_recall` |
| §6.6 | 阶段四 5 特征 + 事实引用 + 待确认字段 | `core/rank.py` | `test_output_has_reasons_and_pending_fields` |
| §6.6 | **LLM 铁律：只重排不造事实** | `core/rank.py::llm_rerank`（越界 `asset_pk` 直接丢弃） | `core/llm.py` 无服务时 `available()==False`，自动走规则基线 |
| §6.6 | 输出规格 | `core/service.py::render_card` + 前端建议单区 | `python -m asset_suggestion demo` + 浏览器目视 |
| §6.7 | 阶段五 G1–G4 | `core/gates.py` | `tests/test_core_gates.py`（每个闸门含触发与不触发两例） |
| §6.8 | 高/中/低三档置信 | `core/rank.py::confidence_band` | `test_confidence_band_values` |
| §7.1 | 交接五要件 + 接手人留空标红 | `core/handover.py` | `test_five_elements_present`、`test_missing_receiver_flagged_not_defaulted` |
| §7.2 | 「发出消息」≠「完成」 | `notified_at` / `receiver_confirmed_at` 分列 | `test_notified_is_not_closed` |
| §7.3 | 超时不自动升级 | `handover_timeout_minutes` 留空 → 只提示 | `test_escalation_not_automatic_when_unconfigured` |
| §7.4 | 基线采集 | `core/metrics.py` | `test_handover_metrics_present` |
| §8.1 | 三道闸门 G-A / G-B / G-C | `core/service.py` + `core/authz.py` | `test_action_permission`、`test_service_enforces_permission` |
| §8.2 | 责任边界 | `core/authz.py` | `test_role_from_actor_id` |
| §8.3 | 授权 = 岗位 × 资料类型 × 业务编号范围，**服务端裁剪** | `core/authz.py::trim_candidates`（按字段并集裁剪，不是按字典键） | `test_server_side_trimming` |
| §9.1 | 写入策略 | `core/schema.sql` 触发器 | `test_asset_snapshot_may_be_overwritten` 对照 `test_update_denied` |
| §9.2 | 哈希链公式 + 断链转只读降级 | `core/audit.py` | `test_chain_verifies_after_writes`、`test_tamper_detected`、`test_service_refuses_when_chain_broken`、`test_degraded_flag_on_break` |
| §9.3 | 规则版本可追溯 | `rule_version` 表 + payload 的 `rule_version`/`model_version` | 建议单与 `/api/status` 均展示 |
| §9.4 | 重建接口映射六节点 | `core/audit.py::rebuild` | `test_rebuild_maps_to_six_nodes` |
| §9.5 | 留存 90 天 / 不自动删 / 双人确认默认关 | `config/default.yaml` | `/api/status` 的「制度性未决项」区 |
| §10.1 | 15 分钟同步周期 + `stale_flag` | `config/default.yaml`、`core/sync.py` | `test_stale_assets_not_recommended` |
| §10.2 | 中断 → 快照降级 + G4 + 显式「暂未更新」 | `core/sync.py::system_banner`、`core/gates.py` | `test_connector_down_triggers_g4`、`test_connector_healthy_no_g4` |
| §10.3 | 重连补齐去重 + 幂等写入 | `core/sync.py::apply_version` | 幂等键 `business_no + source_version` |
| §10.4 | 移动端与弱网 | **未实现**（见第 8 节） | — |
| §11 | 部署拓扑 | **未实现**（本机无容器/PostGIS，见第 8 节） | — |
| §12.2 | 阶段 2 回放验收条件（3 条） | `core/replay.py` | 回放输出「验收条件_*: true」 |
| §12.3 | 98% / 100% 硬线口径与责任人**未确认** | `config/default.yaml` 中两项留空 → **不产出对外达标结论** | `/api/status` 显式列为 null |
| §12.4 | 过程指标 + 分母规则 f53 | `core/metrics.py` | `test_denominator_is_deduplicated_business_no` |
| §13 | 停止条件（含 S4 断链停发建议） | `core/audit.py::chain_status` + `core/service.py` 前置检查 | `test_service_refuses_when_chain_broken` |
| §14.3 | 12 项待确认：设计留口、值待填 | `config/default.yaml` | 见第 6 节 |

---

## 4. 关键实现细节

### 4.1 f45 六字段与留痕结构

| f45 要求 | MVP 落点 |
|---|---|
| 业务编号 | `payload.business_no` |
| 资料版本 | `payload.source_snapshot_version`（参与计算的资产快照版本指纹） |
| 事实引用 | `payload.fact_refs[]`（命中哪条记录、哪个字段、哪个版本） |
| 建议结果 | `payload.suggestion_result.candidates` / `pending_fields` / `confidence_band` |
| 人工改动 | **独立记录** `record_type='human_action'`（`before_value`/`after_value` 成对） |
| 签核人 | `human_action.signed_by` + `actor_id` / `actor_role` |

**唯一事实表是 `audit_record`**（append-only + 全局哈希链）；`suggestion` / `human_action` / `handover` 是其上的视图。
理由：交接状态推进要写新行、人工改动要成对留痕；拆成三张独立表会让哈希链跨表维护，
更容易出现「改了一条对不上链」的漏洞。

哈希链公式与方案一致：

```
record_hash_i = sha256(record_hash_{i-1} ‖ canonical_json(record_i))
```

`canonical_json` = `sort_keys=True` + `separators=(',',':')` + `ensure_ascii=False`（`utils/jsonutil.py`）——
保证同一条记录任何时候序列化结果都相同，否则哈希链不可复现。

### 4.2 匹配层五阶段的关键取舍

- **G1–G3 只对 Top-K 候选判定，G4 全局**：闸门问的是「这条建议能不能直接用」，不是「召回池里有没有可疑资产」。
  早期版本对整个召回池判定 G1，6% 的迁移率导致几乎每单转人工。
- **转人工 ≠ 低置信**：方案 §6.8 中「低」专指无候选或要素冲突。转人工是闸门结论，与匹配置信度是两个独立维度，
  不能互相污染分档。
- **两通道各取 N 后取并集**：早期版本在并集后统一截断到 50，导致「地名说错」场景下锚定内的真值被语义候选挤出，
  Top-1 掉到 75%。改为各通道各取 50（池上限 100），Top-1 回到 89.29%。
- **文本相似度对所有候选生效**：原先只给锚定外的资产生效，导致锚定内候选 `text_score` 恒为 0，
  在池截断时被系统性排挤。

### 4.3 授权裁剪

岗位由 `actor_id` 前缀解析（`OP`/`CO`/`AP`/`SA`/`CS`/`DI`），**不接受调用方声明**——对应 f41「不得借用他人账号」。
裁剪在服务端完成（`core/authz.py::trim_candidates`），按**字段并集**判断，越权字段不下发。

---

## 5. 与《首期系统设计方案》的差异清单（已知边界）

本机没有 fastapi / postgres / docker / ollama / node，所以**所有重依赖都做成了可替换适配器**，
MVP 用标准库跑通。这是已知边界，不是阻塞。

| # | 方案要求 | MVP 实际 | 替换路径 | 风险 |
|---|---|---|---|---|
| 1 | PostgreSQL + PostGIS（`ST_Within`/`ST_DWithin`） | SQLite + 纯 Python（`utils/geo.py`） | `core/recall.py::SpatialIndex` 换 PostGIS 实现 | 点在多边形判定需在生产环境回归 |
| 2 | pgvector 向量近邻 | 字符 bigram Jaccard 近似（`utils/textutil.py`） | 换 pgvector 嵌入检索 | 中文分词近似，召回质量低于真向量 |
| 3 | vLLM / Ollama 本地推理 | `core/llm.py` 适配器；**本机无模型服务，自动降级为规则基线 `rule-baseline-v1`** | 配 `MVP_LLM_BASE_URL` 或改 `config/default.yaml` | 规则基线能力弱于 LLM；但越界丢弃机制已实现 |
| 4 | PaddleOCR 照片结构化 | 未实现（照片字段留空） | 接 PaddleOCR | 外观要素命中目前只有文本来源 |
| 5 | Leaflet / MapLibre 地图 | 内联 SVG 候选分布图（零外部依赖、可离线） | 换地图组件 | 仅示意，非地理精度地图 |
| 6 | SurveyJS 移动端表单 + 离线草稿 | 未实现 | 接 SurveyJS | §10.4 弱网/草稿要求未验证 |
| 7 | §11 部署拓扑（政务专网） | 未实现 | 按方案部署 | 本机无容器环境 |
| 8 | §6.5 召回池「Top-50」 | 两通道各取 50 后取并集（池上限 100），再由阶段四选 Top-3 | 生产可改为并集后按规则分截断至 50 | 池规模翻倍，排序成本略高；这是刻意为之（见 §4.2） |
| 9 | §6.8 置信分档 δ | δ = 0.15 **占位值**（方案要求"先测后定"） | 阶段 2 用真实历史工单标定 | 占位值不可对外使用 |
| 10 | §12.3 验收硬线 | **不产出达标结论** | 业务方填入 `config/default.yaml` | 有意留白 |
| 11 | 真实资产/工单数据 | 全部合成 | 阶段 1 盘点后接真实连接器 | 合成数据不代表真实分布 |

---

## 6. 制度性未决项清单

对应方案 §14.3 的 12 项。**全部做成配置项，默认取保守值**，集中在 `config/default.yaml`，阶段 0 确认后逐项填实。

| # | 待确认项 | 配置项 | 当前默认值（保守） |
|---|---|---|---|
| 1 | 误派责任归属与停止条件复盘责任人 | 停止条件表 | 人工指定 |
| 2 | 正式留存期限、删除执行人、是否双人确认 | `retention_days` / `auto_delete_enabled` / `dual_confirm_enabled` | 90 天 / **不自动删除** / 双人确认**关** |
| 3 | 98% / 100% 的抽样方法与出数责任人 | `acceptance_sampling_method` / `acceptance_metric_owner` | 均留空 → 不产出对外结论 |
| 4 | 对外解释主口径 / 争议判定口径 | 三岗完成时间分别记录 | 不做合并 |
| 5 | 交接「约定时限」具体数值 | `handover_timeout_minutes` | `null`（空且不启用）→ 只提示，不自动升级、不自动改派 |
| 6 | 收益按节点还是按环节归集、人工复核工时由谁提供 | 成本结构单列人工复核工时 | 待填 |
| 7 | 紧急故障 60→20 分钟是否纳入首期验收 | 指标表预留行 | 不启用 |
| 8 | 六节点各自的等待与处理时间 | 指标表预留 | 阶段 1 盘点 |
| 9 | 重复编号数量、无版本迁移记录数量 | `seed.py` 统计输出 | 阶段 1 盘点 |
| 10 | 交接等待时长与重新联系次数基线 | `core/metrics.py` 自动采集 | **无基线**，试点期从零建 |
| 11 | 高峰期终端草稿暂存与同步时间展示可行性 | — | 待系统侧确认 |
| 12 | 回放使用哪批历史资料、并行期长度 | `core/replay.py` 取样入口 | 阶段 2/3 计划 |

---

## 7. 合成数据的构成（避免误读指标）

`core/seed.py` 按难度分五档生成群众原话，模拟「线索多少」：

| 档 | 占比 | 原文形态 | 设计意图 |
|---|---|---|---|
| A | 30% | 地名 + 地标 + 缺陷 | 完整描述，锚定与别名都命中 |
| B | 25% | 只有地标 | 无锚定，只走语义通道 |
| C | 23% | 地标残缺 | 抽到要素但信息不全 |
| D | 12% | 只有地名 + 类别 | 锚定成立，但别名对不上 |
| E | 10% | **地名说错** | 锚定到错误单元，正确资产只能靠语义通道补回——正是 §6.5 两通道并集的理由 |

实测**锚定覆盖率 55%**（56 单中 31 单锚定成功）。
这个数字是**合成数据的特征，不是真实工单的区域线索密度**；真实密度须在阶段 1 盘点时统计。

---

## 8. 后续阶段（方案 §12.1）

1. **阶段 0 · 制度确认**：把第 6 节配置项逐项填实，尤其第 3、5 项——它们决定系统能不能对外出结论。
2. **阶段 1 · 数据盘点**：统计真实工单的区域线索密度、重复编号数、无版本迁移记录数，替换第 7 节的五档假设。
3. **阶段 2 · 真实回放**：用真实历史工单跑同一条路径标定 δ，确定 §12.3 的抽样方法与出数责任人。
4. **阶段 3 · 生产化**：PostGIS + pgvector + 本地 LLM + Leaflet/SurveyJS + 政务专网部署，把适配器切到生产实现。
