from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from gpt_reg.db import schema


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r["name"]) for r in rows}


def migrate(conn: sqlite3.Connection) -> int:
    for stmt in schema.ALL_DDL:
        conn.executescript(stmt)
    # DB tạo từ v1 đã có bảng jobs nên CREATE TABLE IF NOT EXISTS bỏ qua cột mới.
    for table, column, decl in schema.ADD_COLUMNS:
        if column not in _existing_columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.commit()
    row = conn.execute("SELECT MAX(version) AS v FROM _schema_version").fetchone()
    current = int(row["v"]) if row and row["v"] is not None else 0
    if current >= schema.CURRENT_VERSION:
        return current
    for v in range(current + 1, schema.CURRENT_VERSION + 1):
        for stmt in schema.DATA_MIGRATIONS.get(v, ()):
            conn.execute(stmt)
        conn.execute(
            "INSERT INTO _schema_version (version, description) VALUES (?, ?)",
            (v, f"migrate to v{v}"),
        )
    conn.commit()
    return schema.CURRENT_VERSION
