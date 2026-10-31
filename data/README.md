# data/

本目录分两类东西，**提交策略完全不同**：

| 路径 | 内容 | 是否入库 |
|---|---|---|
| `samples/` | 样例数据（小文件，供离线调试与文档引用） | ✅ 入库 |
| `*.db` | 运行时 SQLite（只读镜像 + append-only 审计链） | ❌ 已 `.gitignore` |

## samples/yunzhou_divisions.geojson

云州市**合成**行政区划（前三级：市 / 街道 / 社区），带别名与中心点。
由 `core/districts.py` 导出，用于：

- 离线查看锚定单元边界（阶段二的结果长什么样）
- 调试「群众说的地名能不能锚定上」

> 这是**合成数据**，不是真实区划。生产环境应改用 WTFPL / MIT 的公开区划数据
> （方案 §4.1 选型：`modood/Administrative-divisions-of-China`、`xiangyuecn/AreaCity-JsSpider-StatsGov`）。

## samples/sample_workorders.jsonl

12 条合成的热线受理样本，字段：

| 字段 | 含义 |
|---|---|
| `business_no` | 业务编号 |
| `raw_text` | 群众原话（阶段一输入） |
| `category` | 资产类别 |
| `truth_asset_pk` / `truth_asset_code` | 该样本对应的真实资产（**合成真值**） |
| `truth_location_anchor` | 该资产所属行政单元码 |
| `source_version` | 资产快照版本指纹 |

用途：调试匹配层时用 `truth_*` 字段核对召回与排序是否正确。

> ⚠️ 这里带"真值"只是为了让调试有参照。**合成样本上的命中率只作链路自检**，
> 不是方案 §12.3 的资产定位准确率（那一项的抽样口径与出数责任人尚未确认）。
