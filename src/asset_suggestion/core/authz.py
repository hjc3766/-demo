"""授权与资料范围（对应方案 §8.3 与 f41）。

f41 原文：只有授权的业务岗位可以查看「设施地图 / 照片 / 历史维修 /
迁移记录 / 验收凭证」；涉及最终决定、对外承诺或资产变更，必须由对应
审批人确认，不能借用其他人的账号。

实现要点：
  * 授权判定绑定 actor_id（不绑定可传递的会话上下文），杜绝"借账号"
  * 越权字段在**服务端裁剪后**再下发，不依赖前端隐藏
  * 每一类资料对应到候选资产的哪些字段，裁剪有明确映射而不是整块隐藏
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .config import CONFIG, ROLES, ROLE_ACCOUNT

# 资料类别 → 候选资产中受该资料约束的字段
MATERIAL_FIELDS = {
    "设施地图": ["lng", "lat", "location_anchor"],
    "照片": ["alias_text", "description"],
    "历史维修": ["history_count"],
    "迁移记录": ["migration_version", "prev_lng", "prev_lat", "prev_asset_code"],
    "验收凭证": ["source_version"],
}

# 所有受授权约束的字段（ MATERIAL_FIELDS 各取值的并集）
ALL_MATERIAL_FIELDS = {f for fields in MATERIAL_FIELDS.values() for f in fields}

# 允许执行的动作（方案 §8.2 责任边界表）
ACTION_PERMISSION = {
    "巡查工作人员": {"pause", "amend", "reject", "repair", "contact"},
    "维修协调员": {"accept", "reject", "pause", "amend", "dispatch", "repair",
                   "acceptance", "ledger", "contact"},
    "资产平台工程师": {"accept", "reject", "pause", "amend"},
    "设施安全监督员": {"accept", "reject", "pause", "amend", "escalate"},
    "社区服务代表": set(),
    "设施运营主任": {"accept", "reject", "pause", "amend", "escalate"},
}


class AuthorizationError(PermissionError):
    """越权访问。服务端在裁剪字段前先判定。"""


def role_of(actor_id: str, conn=None) -> str:
    """由 actor_id 解析岗位。

    MVP 约定 actor_id 形如 "CO-0001"（前缀见 config.ROLE_ACCOUNT），
    真实系统应由独立认证服务（方案 §4.1 自建授权目录 / 参考 UnityAuth 思路）
    签发，**不得由调用方自行声明岗位**。
    """
    if not actor_id or "-" not in actor_id:
        raise AuthorizationError(f"无法解析岗位的账号标识：{actor_id!r}")
    prefix = actor_id.split("-", 1)[0].upper()
    for role, pfx in ROLE_ACCOUNT.items():
        if pfx.upper() == prefix:
            return role
    raise AuthorizationError(f"未知账号前缀 {prefix!r}，无对应岗位")


def scope_of(actor_role: str) -> set:
    return set(CONFIG.material_scopes.get(actor_role, set()))


def can(action: str, actor_role: str) -> bool:
    return action in ACTION_PERMISSION.get(actor_role, set())


def require(action: str, actor_role: str) -> None:
    if not can(action, actor_role):
        raise AuthorizationError(f"岗位「{actor_role}」无权执行动作「{action}」")


# 事实引用的前缀 → 所属资料类别（用于裁剪"依据"条目本身）
REF_PREFIX_MATERIAL = {
    "coord": "设施地图",
    "location_anchor": "设施地图",
    "alias_text": "照片",
    "description": "照片",
    "history": "历史维修",
    "source_version": "验收凭证",
    "migration_version": "迁移记录",
    "prev_lng": "迁移记录",
    "prev_lat": "迁移记录",
    "prev_asset_code": "迁移记录",
}


def trim_candidates(candidates: List[dict], actor_role: str) -> List[dict]:
    """按授权范围在服务端裁剪候选字段。越权字段直接不出现在返回体里。"""
    scope = scope_of(actor_role)
    allowed_fields = set()
    for m in scope:
        allowed_fields.update(MATERIAL_FIELDS.get(m, []))
    out = []
    for c in candidates:
        trimmed = {}
        for k, v in c.items():
            if k in ALL_MATERIAL_FIELDS:
                if k in allowed_fields:
                    trimmed[k] = v
                # 否则整个字段不出现（连键都不给），避免前端误用
            else:
                trimmed[k] = v
        if "reasons" in trimmed and isinstance(trimmed["reasons"], list):
            kept = []
            for r in trimmed["reasons"]:
                prefix = (r.get("ref") or "").split(":")[0]
                material = REF_PREFIX_MATERIAL.get(prefix)
                if material is None or material in scope:
                    kept.append(r)
                else:
                    # 越权依据不整条删除，改为只保留中性描述，避免"凭空少了理由"
                    kept.append({"feature": r.get("feature"),
                                 "weight": r.get("weight"),
                                 "detail": "（该依据涉及的材料不在您的授权范围内，已隐去）",
                                 "ref": prefix})
            trimmed["reasons"] = kept
        trimmed["_scope"] = sorted(scope)
        out.append(trimmed)
    return out


def visible_materials(actor_role: str) -> List[str]:
    return sorted(scope_of(actor_role))
