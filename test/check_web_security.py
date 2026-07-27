"""Kiem tra Web API localhost khong dung auth va khong tra du secret cua job."""

from __future__ import annotations

import sqlite3


def main() -> int:
    from fastapi.testclient import TestClient

    from gpt_reg import cli
    from gpt_reg.config import Settings
    from gpt_reg.db import migrate
    from gpt_reg.db import schema
    from gpt_reg.db.repositories import _EXACT_KEYS
    from gpt_reg.web import server

    failures: list[str] = []
    row = {
        "id": "j1",
        "email": "a@example.com",
        "combo": "mail|pass|refresh|client",
        "password": "account-password",
        "session_path": "runtime/sessions/a.json",
        "status": "success",
        "reg_mode": "http",
        "error": None,
    }
    public = server._job_for_api(row)
    leaked = {"combo", "password", "session_path"} & public.keys()
    if leaked:
        failures.append(f"/api/jobs con lo truong nhay cam: {sorted(leaked)}")

    with TestClient(server.app) as client:
        root = client.get("/")
        api = client.get("/api/jobs")

    if root.status_code != 200:
        failures.append(f"root tra {root.status_code}, muon 200 khi khong co auth")
    if api.status_code != 200:
        failures.append(f"API tra {api.status_code}, muon 200 khi khong co auth")
    if "gptreg_session=" in (root.headers.get("set-cookie") or ""):
        failures.append("root van cap cookie auth")
    if 'meta name="auth-token"' in root.text:
        failures.append("root van chen auth token vao HTML")
    if hasattr(server, "_auth") or hasattr(server, "WEB_AUTH_TOKEN"):
        failures.append("backend van con auth gate runtime")

    host_check = getattr(cli, "_is_loopback_web_host", None)
    if not callable(host_check):
        failures.append("CLI thieu ham kiem tra loopback")
    else:
        for host in ("127.0.0.1", "localhost", "::1"):
            if not host_check(host):
                failures.append(f"CLI khong nhan loopback {host}")
        for host in ("0.0.0.0", "10.0.0.9", "example.com"):
            if host_check(host):
                failures.append(f"CLI cho phep bind ngoai loopback: {host}")

    if "web_auth_token" in Settings.__dataclass_fields__:
        failures.append("Settings van con web_auth_token")
    if "web.auth_token" in _EXACT_KEYS:
        failures.append("SQLite settings van expose web.auth_token")

    legacy = sqlite3.connect(":memory:")
    legacy.row_factory = sqlite3.Row
    for statement in schema.ALL_DDL:
        legacy.executescript(statement)
    for version in range(1, 6):
        legacy.execute(
            "INSERT INTO _schema_version (version, description) VALUES (?, ?)",
            (version, f"legacy v{version}"),
        )
    legacy.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)",
        ("web.auth_token", "legacy-secret"),
    )
    legacy.commit()
    migrate(legacy)
    stale = legacy.execute(
        "SELECT 1 FROM settings WHERE key = 'web.auth_token'"
    ).fetchone()
    legacy.close()
    if stale:
        failures.append("migration van giu web.auth_token legacy")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] web security" if failures else "[ok] web security")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
