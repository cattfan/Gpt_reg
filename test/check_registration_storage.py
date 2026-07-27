"""Kiem tra schema va repository cho registration sources."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _legacy_v6_db() -> sqlite3.Connection:
    conn = _memory_db()
    conn.executescript(
        """
        CREATE TABLE _schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now')),
            description TEXT
        );
        CREATE TABLE settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            value TEXT,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        INSERT INTO _schema_version(version, description)
        VALUES (1, 'v1'), (2, 'v2'), (3, 'v3'), (4, 'v4'), (5, 'v5'), (6, 'v6');
        INSERT INTO settings(key, value) VALUES ('proxy.rotation_mode', 'round_robin');
        """
    )
    conn.commit()
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row["name"]) for row in rows}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    return {str(row["name"]) for row in rows}


def _check_schema_and_settings(
    conn: sqlite3.Connection,
    repositories: Any,
    failures: list[str],
) -> None:
    from gpt_reg.db import migrate

    version = migrate(conn)
    if version != 7:
        failures.append(f"migration version={version}, can 7")

    expected_tables = {"mail_rentals", "check_logs", "proxies"}
    missing_tables = expected_tables - _table_names(conn)
    if missing_tables:
        failures.append(f"migration thieu bang: {sorted(missing_tables)}")

    job_columns = {
        str(row["name"]) for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
    }
    expected_columns = {
        "rental_id",
        "source_email",
        "alias_index",
        "profile_region",
        "profile_name",
        "birthdate",
    }
    missing_columns = expected_columns - job_columns
    if missing_columns:
        failures.append(f"migration thieu cot jobs: {sorted(missing_columns)}")

    expected_indexes = {
        "idx_mail_rentals_status",
        "idx_jobs_rental_id",
        "idx_check_logs_check_id",
        "idx_proxies_selected",
    }
    missing_indexes = expected_indexes - _index_names(conn)
    if missing_indexes:
        failures.append(f"migration thieu index: {sorted(missing_indexes)}")

    settings = repositories.SettingsRepository(conn)
    if conn.execute(
        "SELECT 1 FROM settings WHERE key = 'proxy.rotation_mode'"
    ).fetchone():
        failures.append("migration v7 chua xoa proxy.rotation_mode")
    try:
        settings.get("proxy.rotation_mode")
    except KeyError:
        pass
    else:
        failures.append("proxy.rotation_mode van nam trong runtime allowlist")

    values = {
        "proxy.enabled": "true",
        "accstack.api_key": "accstack-secret",
        "mail.smsbower.alias_limit": "3",
        "mail.accstack.alias_limit": "4",
        "sms.smsbower.api_key": "smsbower-secret",
    }
    for key, value in values.items():
        try:
            settings.set(key, value)
        except Exception as exc:
            failures.append(f"settings {key} khong ghi duoc: {type(exc).__name__}: {exc}")

    known = settings.all_known()
    for key in ("accstack.api_key", "sms.smsbower.api_key"):
        if known.get(key) != repositories.MASKED_VALUE:
            failures.append(f"settings {key} khong duoc masked")
    for key in (
        "proxy.enabled",
        "mail.smsbower.alias_limit",
        "mail.accstack.alias_limit",
    ):
        if known.get(key) != values[key]:
            failures.append(f"settings {key} tra sai: {known.get(key)!r}")


def _check_mail_rentals(conn: sqlite3.Connection, cls: Any, failures: list[str]) -> None:
    if cls is None:
        failures.append("thieu MailRentalRepository")
        return
    try:
        repo = cls(conn)
        first = {
            "id": "rental-old",
            "provider": "smsbower",
            "external_id": "external-old",
            "base_email": "old@example.com",
            "product_id": "product-1",
            "status": "active",
            "expires_at": "2026-07-27T12:00:00Z",
            "balance_before": 10.0,
            "balance_after_rent": 9.0,
            "alias_count": 0,
            "created_at": 1.0,
        }
        second = {
            **first,
            "id": "rental-new",
            "external_id": "external-new",
            "base_email": "new@example.com",
            "created_at": 2.0,
        }
        repo.create(first)
        repo.create(second)
        repo.update("rental-old", status="finished", alias_count=2, finished_at=3.0)
        stored = repo.get("rental-old")
        if not stored or stored["status"] != "finished" or stored["alias_count"] != 2:
            failures.append(f"MailRentalRepository update/get sai: {stored!r}")
        recent = repo.list_recent(limit=1)
        if [row["id"] for row in recent] != ["rental-new"]:
            failures.append(f"MailRentalRepository list_recent sai: {recent!r}")
    except Exception as exc:
        failures.append(f"MailRentalRepository loi: {type(exc).__name__}: {exc}")


def _check_proxies(conn: sqlite3.Connection, cls: Any, failures: list[str]) -> None:
    if cls is None:
        failures.append("thieu ProxyRepository")
        return
    try:
        repo = cls(conn)
        repo.replace_all(
            [
                {"value": " 127.0.0.1:8080 ", "selected": True},
                {"value": "user:pass@proxy.example:9000", "selected": False},
            ]
        )
        rows = repo.list_all()
        values = {row["value"]: row for row in rows}
        if set(values) != {"127.0.0.1:8080", "user:pass@proxy.example:9000"}:
            failures.append(f"ProxyRepository khong normalize danh sach: {rows!r}")
        elif values["127.0.0.1:8080"]["selected"] is not True:
            failures.append("ProxyRepository selected khong phai bool")

        before_invalid = repo.list_all()
        try:
            repo.replace_all(
                [
                    {"value": "new.example:7000", "selected": True},
                    {"value": "not-a-proxy", "selected": True},
                ]
            )
        except ValueError:
            pass
        else:
            failures.append("ProxyRepository khong reject proxy sai format")
        if repo.list_all() != before_invalid:
            failures.append("ProxyRepository ghi do dang truoc khi validate het")

        old_id = values["user:pass@proxy.example:9000"]["id"]
        repo.replace_all(
            [
                {"value": "user:pass@proxy.example:9000", "selected": True},
                {"value": "new.example:7000", "selected": False},
            ]
        )
        replaced = repo.list_all()
        replaced_values = {row["value"]: row for row in replaced}
        if set(replaced_values) != {"user:pass@proxy.example:9000", "new.example:7000"}:
            failures.append(f"ProxyRepository replace_all sai: {replaced!r}")
        elif replaced_values["user:pass@proxy.example:9000"]["id"] != old_id:
            failures.append("ProxyRepository khong update row hien co")
    except Exception as exc:
        failures.append(f"ProxyRepository loi: {type(exc).__name__}: {exc}")


def _check_check_logs(conn: sqlite3.Connection, cls: Any, failures: list[str]) -> None:
    if cls is None:
        failures.append("thieu ChecksRepository")
        return
    try:
        repo = cls(conn)
        repo.create(
            {
                "id": "check-retention",
                "email": "retention@example.com",
                "combo": "retention@example.com|secret",
                "status": "error",
                "created_at": 1.0,
            }
        )
        for index in range(505):
            repo.append_log("check-retention", f"line-{index:03d}")
        logs = repo.logs("check-retention", limit=999)
        if len(logs) != 500:
            failures.append(f"ChecksRepository.logs tra {len(logs)} dong, can toi da 500")
        elif logs[0] != "line-005" or logs[-1] != "line-504":
            failures.append(f"ChecksRepository.logs sai thu tu: {logs[:1]}..{logs[-1:]}")

        repo.clear_logs("check-retention")
        if repo.logs("check-retention"):
            failures.append("ChecksRepository.clear_logs chua xoa log")

        repo.append_log("check-retention", "cascade-me")
        repo.delete_by_status(("error",))
        left = conn.execute(
            "SELECT COUNT(*) AS n FROM check_logs WHERE check_id = ?",
            ("check-retention",),
        ).fetchone()
        if left and int(left["n"]) != 0:
            failures.append("check_logs khong cascade khi xoa check")
    except Exception as exc:
        failures.append(f"ChecksRepository logs loi: {type(exc).__name__}: {exc}")


def main() -> int:
    import gpt_reg.db as db
    from gpt_reg.db import repositories

    failures: list[str] = []
    conn = _legacy_v6_db()
    try:
        _check_schema_and_settings(conn, repositories, failures)

        rental_cls = getattr(repositories, "MailRentalRepository", None)
        proxy_cls = getattr(repositories, "ProxyRepository", None)
        checks_cls = getattr(repositories, "ChecksRepository", None)
        _check_mail_rentals(conn, rental_cls, failures)
        _check_proxies(conn, proxy_cls, failures)
        _check_check_logs(conn, checks_cls, failures)

        for name in ("MailRentalRepository", "ProxyRepository", "ChecksRepository"):
            if not hasattr(db, name):
                failures.append(f"gpt_reg.db chua export {name}")
    finally:
        conn.close()

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] registration storage" if failures else "[ok] registration storage")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
