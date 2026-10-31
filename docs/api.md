# 接口说明 —— 建议服务 MVP

三套入口：**HTTP API**（服务间调用）、**命令行**（运维与回放）、**Python API**（嵌入其他系统）。

---

## 1. HTTP API

服务由标准库 `ThreadingHTTPServer` 提供，默认监听 `127.0.0.1:8760`。

```bash
python -m asset_suggestion serve --host 127.0.0.1 --port 8762
```

通用约定：

- 全部响应 `application/json; charset=utf-8`（根路径 `/` 返回前端 HTML）
- `actor_id` **必须**带，岗位由前缀解析，**不接受调用方声明**
  （`OP`=巡查工作人员、`CO`=维修协调员、`AP`=资产平台工程师、`SA`=设施安全监督员、`CS`=社区服务代表、`DI`=设施运营主任）
- 权限不足 → `403`；参数缺失 → `400`；未预期异常 → `500`（带 trace 摘要）

### 1.1 只读端点

| 方法 | 路径 | 查询参数 | 说明 |
|---|---|---|---|
| GET | `/` | — | 前端单页界面 |
| GET | `/api/health` | — | 存活探针，`{"ok":true,"service":"..."}` |
| GET | `/api/status` | — | 连接器心跳、哈希链状态、制度性未决项、规则/模型版本、只读降级标志 |
| GET | `/api/metrics` | — | 方案 §12.4 过程指标（分母为去重业务编号数） |
| GET | `/api/suggestions` | `limit`（默认 50） | 建议记录列表，按业务编号去重 |
| GET | `/api/suggestion/{suggestion_id}` | `actor_id` | 单条建议详情（**按岗位裁剪后**返回） |
| GET | `/api/audit/{business_no}` | — | 按业务编号重建整条链路（六节点） |
| GET | `/api/audit/verify` | — | 哈希链校验结果 |
| GET | `/api/handover/{business_no}` | — | 交接状态（`notified_not_closed` / `closed`） |
| GET | `/api/handover/pending` | — | 待确认交接清单 |
| GET | `/api/replay/latest` | — | 最近一次回放结果 |
| GET | `/api/divisions` | — | 行政区划（含多边形与别名） |
| GET | `/api/assets` | `limit`、`offset` | 资产列表（只读镜像） |

### 1.2 写端点（全部**只写本服务自己的审计表**，不写业务系统）

| 方法 | 路径 | 请求体 | 说明 |
|---|---|---|---|
| POST | `/api/suggest` | `business_no`、`actor_id` | 生成一条建议，返回完整 payload + `card_text`（§6.6 输出规格） |
| POST | `/api/action` | `suggestion_id`、`actor_id`、`action`、`note?`、`after_value?`、`basis_refs?`、`signed_by?` | 记录人工动作（**追加写入，不覆盖原记录**） |
| POST | `/api/handover` | `business_no`、`actor_id`、`receiver?`、`unresolved_items?` | 发起交接；`unresolved_items` 缺省时自动取建议里的 `pending_fields` |
| POST | `/api/handover/confirm` | `business_no`、`actor_id` | 接手人确认（闭环） |
| POST | `/api/replay` | `limit`、`actor_id?` | 触发回放，返回指标与 §12.2 验收条件自检 |
| POST | `/api/sync/heartbeat` | `connector?` | 模拟连接器心跳（默认 `GIS资产库`） |

`action` 可选值与对应岗位权限（见 `core/authz.py` 动作权限矩阵）：

```
accept 采纳建议 / amend 人工改动 / reject 退回 / pause 暂停（现场核对）
dispatch 派工确认（在原系统执行）/ repair 维修完成 / acceptance 验收 / ledger 台账更新
contact 重新联系
```

> 「派工确认」只是**在建议服务里留一条人工动作记录**，真正的派单仍由人在原系统执行（方案 §1.3 P3）。

### 1.3 关键响应样例

`GET /api/suggestion/{sid}?actor_id=CO-0001`（节选）：

```json
{
  "suggestion_id": "SU-21fc2cb58d03",
  "business_no": "WO-202600059",
  "source_snapshot_version": "v1-764d8b1bc13eca7e",
  "result_type": "suggest",
  "trigger_reason": null,
  "rule_version": "MVP-R1",
  "model_version": "rule-baseline-v1",
  "suggestion_result": {
    "confidence_band": "high",
    "recall_count": 50,
    "anchor": { "code": "YZ320102002", "name": "幸福里社区", "centroid_lng": 120.15, "centroid_lat": 30.24 },
    "candidates": [
      {
        "asset_pk": "PK-监控-01017", "asset_code": "WG-监控-01017",
        "lng": 120.158223, "lat": 30.254466,
        "score": 0.74, "in_anchor": true, "dist_m": 436.0,
        "reasons": [ { "feature": "alias_hit", "detail": "地标别名命中「体育馆背面摄像头杆」" } ],
        "pending_fields": ["型号与编号", "资产曾发生迁移且编号未变，须核对是否已失效"]
      }
    ],
    "pending_fields": ["型号与编号", "资产曾发生迁移且编号未变，须核对是否已失效"]
  },
  "fact_refs": [ { "feature": "admin_anchor", "detail": "行政锚定：幸福里社区（YZ320102002）" } ],
  "gates": []
}
```

`GET /api/audit/{business_no}`（节选）：

```json
{
  "business_no": "WO-202600059",
  "record_count": 13,
  "chain": [ { "seq": 56, "record_id": "SU-...", "record_type": "suggestion" } ],
  "node_path": {
    "巡查报修": ["IN-202600059"],
    "资产定位": ["SU-...", "HU-..."],
    "派工": [], "维修": [], "验收": [], "台账更新": []
  },
  "nodes_covered": ["巡查报修", "资产定位"],
  "nodes_missing": ["派工", "维修", "验收", "台账更新"]
}
```

---

## 2. 命令行

```bash
python -m asset_suggestion --help
```

| 命令 | 参数 | 说明 |
|---|---|---|
| `seed` | `--assets`、`--workorders`、`--degraded` | 生成云州合成数据；`--degraded` 模拟连接器中断（触发 G4） |
| `serve` | `--host`、`--port` | 启动 HTTP 服务 |
| `replay` | `-n`（默认 200） | 阶段 2 回放 + 过程指标 + §12.2 验收条件自检 |
| `demo` | `--business-no`、`--actor-id` | 打印一条建议单（§6.6 输出规格） |
| `test` | — | 运行单元测试 |

全局参数 `--db` 指定 SQLite 路径（默认 `config/default.yaml` 里的 `run.db_path`）。

---

## 3. Python API

```python
from asset_suggestion.core.db import session
from asset_suggestion.core.service import SuggestionService, render_card

with session("data/suggestion.db") as conn:
    svc = SuggestionService(conn)
    payload = svc.create_suggestion("WO-202600059", actor_id="CO-0001")
    print(render_card(payload))
```

常用入口：

| 入口 | 作用 |
|---|---|
| `core.service.SuggestionService.create_suggestion(biz, actor_id)` | 走完五阶段，返回建议 payload |
| `core.service.render_card(payload)` | 渲染成方案 §6.6 的人读建议单 |
| `core.service.SuggestionService.act(...)` | 记录人工动作（追加写入） |
| `core.audit.rebuild(conn, business_no)` | 六节点链路重建 |
| `core.audit.chain_status(conn)` | 哈希链校验 |
| `core.metrics.process_metrics(conn)` | 过程指标 |
| `core.replay.run_replay(conn, limit)` | 回放 |

---

## 4. 数据表（只读镜像 + 审计）

| 表 | 写权限 | 说明 |
|---|---|---|
| `admin_division` | 镜像覆盖 | 五级行政区划（含多边形与别名） |
| `asset_snapshot` | **允许覆盖** | 资产只读镜像 + 自建别名库（方案 §9.1 唯一允许的业务例外） |
| `asset_version_snapshot` | 镜像覆盖 | 资产版本历史（用于 G3 版本冲突判定） |
| `hotline_intake` | 镜像覆盖 | 热线受理原文 |
| `workorder_snapshot` | 镜像覆盖 | 工单状态镜像（三岗完成时间分列） |
| `audit_record` | **仅 INSERT** | 唯一事实表：建议 / 人工动作 / 交接，带全局哈希链 |
| `connector_state` | 镜像覆盖 | 连接器心跳 |
| `rule_version` | 镜像覆盖 | 规则版本（生效日期 × 适用范围） |
| `replay_run` | 仅 INSERT | 回放批次与指标 |

视图：`v_suggestion`、`v_human_action`、`v_handover`。

`audit_record` 上挂了行级触发器，**UPDATE / DELETE 会被数据库直接拒绝**——这是 P2 的落点，不是应用层自律。
