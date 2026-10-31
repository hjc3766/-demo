"""通用小工具：canonical JSON。

本模块**不依赖项目内任何其他模块**，可被 core/ 与 utils/ 自由引用。

canonical JSON 的定义（哈希链的基石，方案 §9.2）：
    sort_keys=True + separators=(',', ':') + ensure_ascii=False

即：同一条记录，无论何时序列化、字段顺序如何、是否带空格，得到的字节串都一致，
否则 record_hash = sha256(prev_hash ‖ canonical_json(record)) 就不可复现。
"""
from __future__ import annotations

import json
from typing import Any, Optional


def jload(text: Optional[str], default: Any = None):
    """安全解析 JSON 字段；解析失败返回默认值。"""
    if not text:
        return default if default is not None else []
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def jdump(obj: Any) -> str:
    """canonical JSON：用于哈希链，排序 + 无空格 + 保留中文。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
