"""Check sanitized persistent logs for registration and account checks."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path


def _wait(predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def main() -> int:
    from gpt_reg.db import ChecksRepository, connect, migrate
    from gpt_reg.web.jobs import check_manager as manager_module
    from gpt_reg.web.jobs.reg_manager import sanitize_job_log_line

    failures: list[str] = []
    samples = {
        "factor_id=abc123]": "factor_id=[redacted]",
        "code=[123456]": "code=[redacted]",
        "OTP 123456": "OTP [redacted]",
    }
    for raw, expected in samples.items():
        actual = sanitize_job_log_line(raw)
        if actual != expected:
            failures.append(f"sanitizer {raw!r} -> {actual!r}, want {expected!r}")

    conn = connect(Path(tempfile.mkdtemp()) / "logs.db")
    migrate(conn)
    repo = ChecksRepository(conn)
    manager = manager_module.CheckManager()
    events: list[dict] = []
    manager.subscribe(events.append)
    call = 0

    def fake_check(_combo, _proxy, log, **_kwargs):
        nonlocal call
        call += 1
        if call == 1:
            log("[mail] OTP 123456")
            log("[mfa] factor_id=abc123]")
        else:
            log("[retry] code=654321]")
        return {"plan": "free", "email": "fixture@example.com"}

    original = manager_module.check_account
    manager_module.check_account = fake_check
    try:
        ids = manager.start_batch(
            combos=["fixture@example.com|Passw0rd123|JBSWY3DPEHPK3PXP"],
            checks_repo=repo,
            proxy_pool_text="",
            concurrency=1,
        )
        if not _wait(lambda: not manager.running):
            failures.append("initial check batch did not finish")
            return len(failures)
        lines = repo.logs(ids[0], limit=500)
        expected_lines = ["[mail] OTP [redacted]", "[mfa] factor_id=[redacted]"]
        if lines != expected_lines:
            failures.append(f"persisted check logs are wrong: {lines!r}")
        event_lines = [event.get("line") for event in events if event.get("type") == "check_log"]
        if event_lines != expected_lines:
            failures.append(f"SSE check logs differ from persisted logs: {event_lines!r}")

        manager.start_batch(
            combos=[],
            checks_repo=repo,
            proxy_pool_text="",
            concurrency=1,
            check_ids=ids,
        )
        if not _wait(lambda: not manager.running):
            failures.append("retry check batch did not finish")
        retry_lines = repo.logs(ids[0], limit=500)
        if retry_lines != ["[retry] code=[redacted]"]:
            failures.append(f"retry did not clear/sanitize logs: {retry_lines!r}")
    finally:
        manager_module.check_account = original
        conn.close()

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] persistent logs" if failures else "[ok] persistent logs")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
