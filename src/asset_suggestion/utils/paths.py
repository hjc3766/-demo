"""通用小工具：路径解析。

解决 src/ 布局下的三个常见问题：

1. **项目根在哪**：包目录是 ``<root>/src/asset_suggestion``，项目根是它的上上级。
   装了包（site-packages）以后没有项目根，此时全部退回到当前工作目录。
2. **资源在哪**（schema.sql、web/index.html、config/default.yaml）。
3. **默认数据库在哪**：优先 ``<root>/data/suggestion.db``，装包后退回 ``./data/suggestion.db``。

本模块**不依赖项目内任何其他模块**。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def package_dir() -> Path:
    """包目录 src/asset_suggestion/。"""
    return Path(__file__).resolve().parents[1]


def project_root() -> Optional[Path]:
    """项目根目录（含 pyproject.toml 与 config/ 的那一层）；装包后返回 None。"""
    for parent in package_dir().parents:
        if (parent / "pyproject.toml").is_file() or (parent / "config" / "default.yaml").is_file():
            return parent
    return None


def config_path() -> Optional[Path]:
    """非机密默认配置文件 config/default.yaml；不存在返回 None。"""
    root = project_root()
    return root / "config" / "default.yaml" if root else None


def data_dir() -> Path:
    """运行时数据目录 data/（放 SQLite 库，已被 .gitignore 排除）。"""
    root = project_root()
    return (root / "data") if root else Path("data")


def default_db_path() -> str:
    """默认 SQLite 路径。"""
    return str(data_dir() / "suggestion.db")


def ensure_parent_dir(file_path: str) -> None:
    """确保父目录存在。"""
    parent = os.path.dirname(os.path.abspath(file_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
