"""Check global proxy toggle and selected-subset random routing."""

from __future__ import annotations

import tempfile
from pathlib import Path


def _runtime_context(records, *, enabled: bool):
    from gpt_reg.config import Settings
    from gpt_reg.db import ProxyRepository, SettingsRepository, connect, migrate
    from gpt_reg import signup

    root = Path(tempfile.mkdtemp())
    settings = Settings(root_dir=root, runtime_dir=root / "runtime")
    conn = connect(settings.runtime_dir / "data.db")
    migrate(conn)
    SettingsRepository(conn).set("proxy.enabled", "true" if enabled else "false")
    ProxyRepository(conn).replace_all(records)
    conn.close()

    previous_conn = signup._shared_conn
    previous_root = signup._shared_root
    signup._shared_conn = None
    signup._shared_root = None
    try:
        context = signup._build_context(settings)
        return context, signup._shared_conn
    finally:
        signup._shared_conn = previous_conn
        signup._shared_root = previous_root


def main() -> int:
    import gpt_reg.proxy.pool as pool_module

    failures: list[str] = []
    records = [
        {"value": "one.example:8001", "selected": False},
        {"value": "two.example:8002", "selected": True},
        {"value": "three.example:8003", "selected": True},
    ]

    disabled = pool_module.ProxyPool.from_records(records, enabled=False)
    if disabled.acquire_url() is not None:
        failures.append("disabled proxy pool must use direct connection")

    populations: list[tuple[str, ...]] = []
    original_choice = pool_module.secrets.choice

    def choose_first(values):
        populations.append(tuple(values))
        return values[0]

    pool_module.secrets.choice = choose_first
    try:
        selected = pool_module.ProxyPool.from_records(records, enabled=True)
        if selected.acquire_url() != "http://two.example:8002":
            failures.append("selected subset did not choose the first selected proxy")
        if populations[-1] != ("two.example:8002", "three.example:8003"):
            failures.append(f"wrong selected candidates: {populations[-1]!r}")

        none_selected = pool_module.ProxyPool.from_records(
            [{**row, "selected": False} for row in records],
            enabled=True,
        )
        none_selected.acquire_url()
        if populations[-1] != tuple(row["value"] for row in records):
            failures.append(f"none-selected must mean all: {populations[-1]!r}")
    finally:
        pool_module.secrets.choice = original_choice

    try:
        pool_module.ProxyPool.from_records([], enabled=True)
        failures.append("enabled empty proxy pool must fail fast")
    except ValueError:
        pass

    legacy_direct = pool_module.ProxyPool.from_multiline("")
    if legacy_direct.acquire_url() is not None:
        failures.append("empty legacy pool must remain direct for CLI compatibility")

    for invalid in (
        [{"value": "one.example:8001", "selected": "yes"}],
        [{"value": "not-a-proxy", "selected": True}],
    ):
        try:
            pool_module.ProxyPool.from_records(invalid, enabled=True)
            failures.append(f"invalid records must fail: {invalid!r}")
        except ValueError:
            pass

    runtime_records = [
        {"value": "runtime-one.example:8101", "selected": True},
        {"value": "runtime-two.example:8102", "selected": False},
    ]
    original_choice = pool_module.secrets.choice
    pool_module.secrets.choice = lambda values: values[0]
    try:
        context, context_conn = _runtime_context(runtime_records, enabled=True)
        if context.proxy_pool.acquire_url() != "http://runtime-one.example:8101":
            failures.append("runtime context did not read selected SQLite proxies")
        context_conn.close()

        direct_context, direct_conn = _runtime_context(runtime_records, enabled=False)
        if direct_context.proxy_pool.acquire_url() is not None:
            failures.append("runtime proxy toggle off did not force direct connection")
        direct_conn.close()

        try:
            _context, empty_conn = _runtime_context([], enabled=True)
            empty_conn.close()
            failures.append("runtime enabled-empty proxy config must fail fast")
        except ValueError:
            pass
    finally:
        pool_module.secrets.choice = original_choice

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] proxy selection" if failures else "[ok] proxy selection")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
