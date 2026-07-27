"""Kiểm tra chế độ đa luồng: chạy song song thật, huỷ được, và không tranh SQLite.

Điểm dễ sai: worker chết lặng lẽ để job treo `running` mãi; `stop_all` chỉ huỷ
được job đầu tiên; nhiều luồng ghi SQLite cùng lúc dính `database is locked`.
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path

CLIENT_ID = "12345678-1234-1234-1234-123456789abc"


def _fresh_repo():
    from gpt_reg.db import connect, migrate
    from gpt_reg.db.repositories import JobRepository

    tmp = Path(tempfile.mkdtemp()) / "test.db"
    conn = connect(tmp)
    migrate(conn)
    return JobRepository(conn)


def _combos(n: int) -> list[str]:
    return [f"user{i}@hotmail.com|Passw0rd123|refresh{i}|{CLIENT_ID}" for i in range(n)]


def _wait(predicate, timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _check_parallel(failures: list[str]) -> None:
    """8 job / 4 luồng: phải có ít nhất 4 job chạy CÙNG LÚC."""
    from gpt_reg.models import SignupResult
    from gpt_reg.web.jobs import reg_manager as mod

    repo = _fresh_repo()
    manager = mod.RegJobManager()
    live = 0
    peak = 0
    guard = threading.Lock()

    def fake_run_signup(request, *, log=None, with_2fa=False, should_cancel=None, **_kw):
        nonlocal live, peak
        with guard:
            live += 1
            peak = max(peak, live)
        time.sleep(0.4)
        with guard:
            live -= 1
        return SignupResult(ok=True, email=request.email, exit_code=0)

    original = mod.run_signup
    mod.run_signup = fake_run_signup
    try:
        ids = manager.start_batch(
            combos=_combos(8), headless=True, jobs_repo=repo, ctx=object(),
            reg_mode="http", concurrency=4,
        )
        if len(ids) != 8:
            failures.append(f"tạo {len(ids)} job (muốn 8)")
        if not _wait(lambda: not manager.running, timeout_s=25):
            failures.append("batch không kết thúc")
        if peak < 4:
            failures.append(f"đỉnh song song chỉ {peak} (muốn >= 4) — không chạy đa luồng")
        done = [r for r in repo.list_recent() if r["status"] == "success"]
        if len(done) != 8:
            failures.append(f"{len(done)}/8 job success")
    finally:
        mod.run_signup = original


def _check_stop_all_multi(failures: list[str]) -> None:
    """stop_all phải huỷ MỌI job đang chạy, không chỉ job đầu."""
    from gpt_reg.models import SignupResult
    from gpt_reg.web.jobs import reg_manager as mod

    repo = _fresh_repo()
    manager = mod.RegJobManager()
    started = threading.Event()
    running_count = 0
    guard = threading.Lock()

    def fake_run_signup(request, *, log=None, with_2fa=False, should_cancel=None, **_kw):
        nonlocal running_count
        with guard:
            running_count += 1
            if running_count >= 3:
                started.set()
        for _ in range(300):
            if should_cancel and should_cancel():
                return SignupResult(ok=False, email=request.email, error="cancelled", exit_code=3)
            time.sleep(0.02)
        return SignupResult(ok=True, email=request.email, exit_code=0)

    original = mod.run_signup
    mod.run_signup = fake_run_signup
    try:
        manager.start_batch(
            combos=_combos(6), headless=True, jobs_repo=repo, ctx=object(),
            reg_mode="http", concurrency=3,
        )
        if not started.wait(timeout=10):
            failures.append("3 job không cùng chạy được")
            return
        manager.stop_all()
        if not _wait(lambda: not manager.running, timeout_s=20):
            failures.append("stop_all không dừng được batch")
        statuses = {r["status"] for r in repo.list_recent()}
        if "running" in statuses:
            failures.append(f"còn job kẹt running sau stop_all: {statuses}")
        cancelled = [r for r in repo.list_recent() if r["status"] == "cancelled"]
        if len(cancelled) < 3:
            failures.append(f"chỉ {len(cancelled)} job bị huỷ — stop_all bỏ sót")
    finally:
        mod.run_signup = original


def _check_worker_crash(failures: list[str]) -> None:
    """Job làm worker nổ không được để trạng thái treo `running`."""
    from gpt_reg.web.jobs import reg_manager as mod

    repo = _fresh_repo()
    manager = mod.RegJobManager()

    def boom(request, **_kw):
        raise RuntimeError("nổ có chủ đích")

    original = mod.run_signup
    mod.run_signup = boom
    try:
        manager.start_batch(
            combos=_combos(3), headless=True, jobs_repo=repo, ctx=object(),
            reg_mode="http", concurrency=2,
        )
        if not _wait(lambda: not manager.running, timeout_s=15):
            failures.append("batch không kết thúc sau khi worker nổ")
        stuck = [r for r in repo.list_recent() if r["status"] in ("running", "queued")]
        if stuck:
            failures.append(f"{len(stuck)} job kẹt sau khi worker nổ")
    finally:
        mod.run_signup = original


def _check_clamp(failures: list[str]) -> None:
    from gpt_reg.web.jobs.reg_manager import (
        CONCURRENCY_CHOICES,
        MAX_CONCURRENCY_BROWSER,
        MAX_CONCURRENCY_HTTP,
        clamp_concurrency,
    )

    if CONCURRENCY_CHOICES != (1, 2, 5, 10, 20, 50, 100, 200):
        failures.append(f"mức luồng sai: {CONCURRENCY_CHOICES}")
    # Browser phải bị chặn trần vì mỗi Camoufox tốn ~300 MB.
    if clamp_concurrency(200, "browser") != MAX_CONCURRENCY_BROWSER:
        failures.append("browser không bị chặn trần")
    if clamp_concurrency(200, "http") != 200:
        failures.append("http bị chặn nhầm")
    if clamp_concurrency(999, "http") != MAX_CONCURRENCY_HTTP:
        failures.append("http không chặn trần trên")
    for bad in (0, -5, None, "abc"):
        if clamp_concurrency(bad, "http") != 1:
            failures.append(f"giá trị lạ {bad!r} phải về 1")


def main() -> int:
    failures: list[str] = []
    _check_clamp(failures)
    _check_parallel(failures)
    _check_stop_all_multi(failures)
    _check_worker_crash(failures)
    for line in failures:
        print(f"[fail] {line}")
    print("[fail] concurrency" if failures else "[ok] concurrency")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
