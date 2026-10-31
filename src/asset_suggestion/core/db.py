"""数据库访问层。"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Optional

from .config import CONFIG

# canonical JSON 已下沉到 utils/；这里保留同名导出，
# 便于 `from .db import jdump, jload` 的既有调用点不必逐个改。
from ..utils.jsonutil import jdump, jload  # noqa: F401

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or CONFIG.db_path
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    # check_same_thread=False：HTTP 服务用 ThreadingHTTPServer，连接被多线程共享。
    # 安全性由 api.py 的 DB_LOCK 串行化保证（见下）。
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """建表（幂等）。"""
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()


@contextmanager
def session(db_path: Optional[str] = None):
    conn = connect(db_path)
    try:
        init_db(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list:
    return [dict(r) for r in rows]
