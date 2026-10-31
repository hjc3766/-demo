"""全局配置。

设计原则（对应《首期系统设计方案》§2 末段）：
所有制度性未决项一律做成可配置项，默认取保守值，而不是替业务方预设。

配置来源有两层，后者覆盖前者：
  1. 本文件里的 dataclass 默认值（权威兜底，装包后没有 config/ 也能跑）；
  2. 项目根目录的 `config/default.yaml`（**非机密**默认配置，阶段 0 制度确认后
     在这里逐项填实，不用改代码）。

`config/default.yaml` 只放非机密项。口令、密钥、证书一律不进仓库（见 .gitignore）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from ..utils import paths
from ..utils.yamllite import load_yaml_file

# 岗位（对应方案 §8.2 责任边界表）
ROLES = {
    "巡查工作人员": "patrol",
    "维修协调员": "coordinator",
    "资产平台工程师": "platform",
    "设施安全监督员": "safety",
    "社区服务代表": "community",
    "设施运营主任": "director",
}

# 岗位 → 账号前缀（种子数据用）
ROLE_ACCOUNT = {
    "巡查工作人员": "OP",
    "维修协调员": "CO",
    "资产平台工程师": "AP",
    "设施安全监督员": "SA",
    "社区服务代表": "CS",
    "设施运营主任": "DI",
}

# 授权默认范围（f41 五类资料）；可被 config/default.yaml 覆盖
DEFAULT_MATERIAL_SCOPES = {
    "巡查工作人员": ["设施地图", "照片", "历史维修"],
    "维修协调员": ["设施地图", "照片", "历史维修", "迁移记录", "验收凭证"],
    "资产平台工程师": ["设施地图", "照片", "历史维修", "迁移记录", "验收凭证"],
    "设施安全监督员": ["设施地图", "照片", "历史维修", "迁移记录", "验收凭证"],
    "社区服务代表": ["历史维修"],
    "设施运营主任": ["设施地图", "照片", "历史维修", "迁移记录", "验收凭证"],
}


@dataclass
class Config:
    # ---- 运行 ----
    db_path: str = field(default_factory=paths.default_db_path)
    seed_assets: int = 2000          # 演示规模；生产为 14000（f12）
    seed_workorders: int = 300
    sync_period_minutes: int = 15     # f34 已授权业务状态更新周期

    # ---- 制度性未决项（默认保守值，待业务方确认后填入）----
    retention_days: int = 90          # f43 评价记录暂留期（暂定值）
    auto_delete_enabled: bool = False     # f43 默认不自动删除
    dual_confirm_enabled: bool = False    # f43 双人确认开关，默认关
    handover_timeout_minutes: Optional[int] = None  # f25 约定时限未确认 → 空且不启用
    acceptance_sampling_method: Optional[str] = None  # 方案 §12.3 抽样方法未确认
    acceptance_metric_owner: Optional[str] = None      # 方案 §12.3 出数责任人未确认

    # ---- 匹配层参数（方案 §6）----
    recall_radius_m: int = 400        # 锚定单元内的空间邻近半径
    recall_limit: int = 50            # 单通道 Top-50 候选
    top_k: int = 3                    # Top-3 输出
    confidence_delta: float = 0.15    # δ 占位值；阶段 2 回放标定后才可对外使用

    # ---- 模型服务（专网内，数据不出域；无服务时自动降级为规则基线）----
    llm_base_url: Optional[str] = os.environ.get("MVP_LLM_BASE_URL")
    llm_model: str = os.environ.get("MVP_LLM_MODEL", "qwen2.5:7b")
    llm_timeout_s: int = 30
    llm_enabled: bool = bool(os.environ.get("MVP_LLM_BASE_URL"))

    # ---- 授权（f41 五类资料）----
    material_scopes: dict = field(default_factory=lambda: dict(DEFAULT_MATERIAL_SCOPES))

    def to_dict(self) -> dict:
        return asdict(self)

    def ensure_dirs(self) -> None:
        d = os.path.dirname(os.path.abspath(self.db_path))
        if d:
            os.makedirs(d, exist_ok=True)


# ---------------------------------------------------------------- YAML 覆盖

def _pick(section: dict, key: str, current):
    """只有 YAML 里显式出现该键才覆盖（区分"没写"与"写成 null"）。"""
    if isinstance(section, dict) and key in section:
        return section[key]
    return current


def _apply_yaml(cfg: Config, data: dict) -> Config:
    run = data.get("run") or {}
    policy = data.get("policy") or {}
    matching = data.get("matching") or {}
    llm = data.get("llm") or {}
    scopes = data.get("material_scopes")

    cfg.db_path = _pick(run, "db_path", cfg.db_path)
    cfg.seed_assets = _pick(run, "seed_assets", cfg.seed_assets)
    cfg.seed_workorders = _pick(run, "seed_workorders", cfg.seed_workorders)
    cfg.sync_period_minutes = _pick(run, "sync_period_minutes", cfg.sync_period_minutes)

    cfg.retention_days = _pick(policy, "retention_days", cfg.retention_days)
    cfg.auto_delete_enabled = _pick(policy, "auto_delete_enabled", cfg.auto_delete_enabled)
    cfg.dual_confirm_enabled = _pick(policy, "dual_confirm_enabled", cfg.dual_confirm_enabled)
    cfg.handover_timeout_minutes = _pick(
        policy, "handover_timeout_minutes", cfg.handover_timeout_minutes)
    cfg.acceptance_sampling_method = _pick(
        policy, "acceptance_sampling_method", cfg.acceptance_sampling_method)
    cfg.acceptance_metric_owner = _pick(
        policy, "acceptance_metric_owner", cfg.acceptance_metric_owner)

    cfg.recall_radius_m = _pick(matching, "recall_radius_m", cfg.recall_radius_m)
    cfg.recall_limit = _pick(matching, "recall_limit", cfg.recall_limit)
    cfg.top_k = _pick(matching, "top_k", cfg.top_k)
    cfg.confidence_delta = _pick(matching, "confidence_delta", cfg.confidence_delta)

    cfg.llm_base_url = _pick(llm, "base_url", cfg.llm_base_url)
    cfg.llm_model = _pick(llm, "model", cfg.llm_model)
    cfg.llm_timeout_s = _pick(llm, "timeout_s", cfg.llm_timeout_s)
    enabled = _pick(llm, "enabled", None)
    # 保守策略：只有"确实有服务地址"才启用；YAML 里显式写 enabled: false 则强制关闭
    cfg.llm_enabled = bool(cfg.llm_base_url) if enabled is None else bool(enabled)

    if isinstance(scopes, dict) and scopes:
        cfg.material_scopes = {k: list(v or []) for k, v in scopes.items()}

    # 相对路径按项目根解析
    if cfg.db_path and not os.path.isabs(cfg.db_path):
        root = paths.project_root()
        cfg.db_path = os.path.normpath(os.path.join(str(root), cfg.db_path)) if root else cfg.db_path
    return cfg


def load_config() -> Config:
    """读 config/default.yaml（若存在）并覆盖默认值。"""
    cfg = Config()
    path = paths.config_path()
    if path and os.path.isfile(path):
        try:
            data = load_yaml_file(str(path)) or {}
        except Exception:
            # 配置读不出来时用代码默认值兜底，但必须让调用方知道
            raise RuntimeError(f"配置文件解析失败：{path}") from None
        if isinstance(data, dict):
            cfg = _apply_yaml(cfg, data)
    return cfg


CONFIG = load_config()
