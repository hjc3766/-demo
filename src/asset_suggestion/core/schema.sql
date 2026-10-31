-- 云州公共设施报修与资产协同 · 建议服务 MVP 数据结构
-- 严格对应《首期系统设计方案》§5 数据模型与 §9.1 写入策略。
--
-- 写入策略（P2「建议永远不覆盖历史」）：
--   * audit_record  —— 唯一审计事实表，**仅 INSERT**，用触发器在数据库层禁止 UPDATE/DELETE
--   * asset_snapshot / workorder_snapshot —— 镜像表，允许覆盖（真相在源系统）
--   * 业务系统三表 —— 本服务完全无写权限（模拟层由 adapter 层保证，见 connectors.py）

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- 行政区划（WTFPL/MIT 五级区划数据的替代实现）
CREATE TABLE IF NOT EXISTS admin_division (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    level       INTEGER NOT NULL,           -- 1 省/市 2 区县 3 街道 4 社区
    parent_code TEXT,
    lat         REAL,
    lng         REAL,
    polygon     TEXT NOT NULL DEFAULT '[]', -- JSON [[lng,lat], ...]
    aliases     TEXT NOT NULL DEFAULT '[]', -- JSON 群众口语别名
    source      TEXT NOT NULL DEFAULT 'self-built'
);

-- ---------------------------------------------------------------- 资产快照（只读镜像 + 自建别名）
CREATE TABLE IF NOT EXISTS asset_snapshot (
    asset_pk        TEXT PRIMARY KEY,       -- 主编号
    asset_code      TEXT NOT NULL,          -- 业务编号
    asset_category  TEXT NOT NULL,          -- 资产类别
    alias_text      TEXT NOT NULL DEFAULT '[]',  -- 地标别名（自建关键字段）
    description     TEXT NOT NULL DEFAULT '',
    lng             REAL,
    lat             REAL,
    coord_sys       TEXT,                   -- 坐标系标识
    location_anchor TEXT,                   -- 指向 admin_division.code
    migration_version TEXT,                 -- 资产迁移版本号
    prev_lng        REAL,
    prev_lat        REAL,
    prev_asset_code TEXT,                   -- 迁移时的业务编号（未变则触发 G1）
    source_version  TEXT,                   -- 源系统数据版本指纹
    ambiguity_flag  INTEGER NOT NULL DEFAULT 0,
    ambiguity_reason TEXT NOT NULL DEFAULT '[]',
    stale_flag      INTEGER NOT NULL DEFAULT 0,
    fetched_at      TEXT,
    last_moved_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_asset_cat     ON asset_snapshot(asset_category);
CREATE INDEX IF NOT EXISTS ix_asset_anchor ON asset_snapshot(location_anchor);
CREATE INDEX IF NOT EXISTS ix_asset_code   ON asset_snapshot(asset_code);

-- 资产多版本快照（G3「两个版本冲突」的判定依据）
CREATE TABLE IF NOT EXISTS asset_version_snapshot (
    asset_pk       TEXT NOT NULL,
    source_version TEXT NOT NULL,
    lng            REAL,
    lat            REAL,
    coord_sys      TEXT,
    fetched_at     TEXT,
    PRIMARY KEY (asset_pk, source_version)
);

-- ---------------------------------------------------------------- 热线受理（群众原话，只读镜像）
CREATE TABLE IF NOT EXISTS hotline_intake (
    intake_id   TEXT PRIMARY KEY,
    business_no TEXT NOT NULL,
    raw_text    TEXT NOT NULL,
    photos      TEXT NOT NULL DEFAULT '[]',
    received_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_intake_biz ON hotline_intake(business_no);

-- ---------------------------------------------------------------- 工单状态（只读镜像）
CREATE TABLE IF NOT EXISTS workorder_snapshot (
    business_no     TEXT PRIMARY KEY,
    status          TEXT NOT NULL,
    category        TEXT,
    assignee        TEXT,
    dept            TEXT,
    updated_at      TEXT,
    community_done_at TEXT,   -- 社区：现场恢复（f50）
    repair_done_at    TEXT,   -- 维修：完成作业
    ledger_done_at    TEXT,   -- 资产库：更新完成
    truth_asset_pk  TEXT      -- 仅回放样本使用：标准答案
);
CREATE INDEX IF NOT EXISTS ix_wo_assignee ON workorder_snapshot(assignee);

-- ---------------------------------------------------------------- 连接器心跳（G4「关键系统不可用」）
CREATE TABLE IF NOT EXISTS connector_state (
    connector      TEXT PRIMARY KEY,
    last_heartbeat TEXT,
    status         TEXT NOT NULL DEFAULT 'unknown'
);

-- ---------------------------------------------------------------- 规则版本（f24：按生效日期 × 适用范围选版本）
CREATE TABLE IF NOT EXISTS rule_version (
    rule_name      TEXT NOT NULL,
    rule_version   TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to   TEXT,
    scope          TEXT NOT NULL DEFAULT 'all',
    payload        TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (rule_name, rule_version)
);

-- ---------------------------------------------------------------- 审计事实表（append-only + 哈希链）
CREATE TABLE IF NOT EXISTS audit_record (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id     TEXT UNIQUE NOT NULL,
    record_type   TEXT NOT NULL,          -- suggestion | human_action | handover
    business_no   TEXT NOT NULL,
    suggestion_id TEXT,
    actor_id      TEXT,
    actor_role    TEXT,
    payload       TEXT NOT NULL,          -- canonical JSON
    created_at    TEXT NOT NULL,
    prev_hash     TEXT NOT NULL,
    record_hash   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_biz ON audit_record(business_no, seq);
CREATE INDEX IF NOT EXISTS ix_audit_sug ON audit_record(suggestion_id);

-- P2 在数据库层的强制：审计表禁止 UPDATE / DELETE
CREATE TRIGGER IF NOT EXISTS trg_audit_no_update
BEFORE UPDATE ON audit_record
BEGIN
    SELECT RAISE(ABORT, 'P2 violation: audit_record is append-only, UPDATE denied');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
BEFORE DELETE ON audit_record
BEGIN
    SELECT RAISE(ABORT, 'P2 violation: audit_record is append-only, DELETE denied');
END;

-- ---------------------------------------------------------------- 便于查询的视图
CREATE VIEW IF NOT EXISTS v_suggestion AS
SELECT seq, record_id AS suggestion_id, business_no, created_at,
       record_hash, prev_hash, payload
FROM audit_record WHERE record_type = 'suggestion';

CREATE VIEW IF NOT EXISTS v_human_action AS
SELECT seq, record_id AS action_id, business_no, suggestion_id,
       actor_id, actor_role, created_at, record_hash, payload
FROM audit_record WHERE record_type = 'human_action';

CREATE VIEW IF NOT EXISTS v_handover AS
SELECT seq, record_id AS record_id, business_no, created_at,
       record_hash, payload
FROM audit_record WHERE record_type = 'handover';

-- ---------------------------------------------------------------- 重建索引（f45：按业务编号重建全链路）
CREATE TABLE IF NOT EXISTS audit_index (
    business_no TEXT PRIMARY KEY,
    chain_ids   TEXT NOT NULL DEFAULT '[]',
    node_path   TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT
);

-- ---------------------------------------------------------------- 回放运行记录（方案 §12.2）
CREATE TABLE IF NOT EXISTS replay_run (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    limit_n     INTEGER,
    sample_n    INTEGER,
    excluded_ambiguous INTEGER DEFAULT 0,
    metrics     TEXT NOT NULL DEFAULT '{}',
    note        TEXT
);
