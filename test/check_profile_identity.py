"""Check deterministic profile identities for registration jobs."""

from __future__ import annotations

import tempfile
import time
from datetime import date
from pathlib import Path


_CLIENT_ID = "12345678-1234-1234-1234-123456789abc"


def _wait(predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _check_job_persistence(failures: list[str]) -> None:
    from gpt_reg.db import JobRepository, connect, migrate
    from gpt_reg.models import SignupResult
    from gpt_reg.web.jobs import reg_manager as manager_module

    conn = connect(Path(tempfile.mkdtemp()) / "identity.db")
    migrate(conn)
    repo = JobRepository(conn)
    manager = manager_module.RegJobManager()
    captured = []

    def fake_run_signup(request, **_kwargs):
        captured.append(request)
        return SignupResult(ok=True, email=request.email)

    original = manager_module.run_signup
    manager_module.run_signup = fake_run_signup
    try:
        ids = manager.start_batch(
            combos=[f"identity@hotmail.com|MailPass123|refresh|{_CLIENT_ID}"],
            headless=True,
            jobs_repo=repo,
            reg_mode="http",
            profile_region="ko",
        )
        if not _wait(lambda: not manager.running):
            failures.append("profile job did not finish")
            return
        row = repo.get(ids[0])
        if not row or row.get("profile_region") != "ko":
            failures.append(f"profile region was not persisted: {row!r}")
            return
        if not row.get("profile_name") or not row.get("birthdate"):
            failures.append("profile name/birthdate were not persisted")
        if not captured or captured[0].name != row.get("profile_name"):
            failures.append("SignupRequest did not use persisted profile name")
        if captured and captured[0].birthdate != row.get("birthdate"):
            failures.append("SignupRequest did not use persisted birthdate")

        manager.start_batch(
            combos=[],
            headless=True,
            jobs_repo=repo,
            reg_mode="http",
            profile_region="vi",
            job_ids=ids,
        )
        if not _wait(lambda: not manager.running):
            failures.append("profile retry did not finish")
        retried = repo.get(ids[0])
        if not retried or retried.get("profile_region") != "ko":
            failures.append("retry replaced the persisted profile region")
        if len(captured) >= 2 and captured[1].name != row.get("profile_name"):
            failures.append("retry regenerated the profile identity")
    finally:
        manager_module.run_signup = original
        conn.close()


def main() -> int:
    from gpt_reg.profile_identity import age_on, generate_profile_identity

    failures: list[str] = []
    today = date(2026, 7, 27)

    for region in ("vi", "ko", "in"):
        first = generate_profile_identity(region, seed="job-123", today=today)
        second = generate_profile_identity(region, seed="job-123", today=today)
        if first != second:
            failures.append(f"{region}: identity is not deterministic")
        age = age_on(date.fromisoformat(first.birthdate), today)
        if not 18 <= age <= 45:
            failures.append(f"{region}: age outside 18-45: {age}")
        if first.region != region or not first.name.strip():
            failures.append(f"{region}: invalid identity payload")

    vi = generate_profile_identity("vi", seed="vi-seed", today=today)
    if vi.name.isascii():
        failures.append("Vietnamese name must preserve diacritics")

    ko = generate_profile_identity("ko", seed="ko-seed", today=today)
    if not any("\uac00" <= char <= "\ud7a3" for char in ko.name):
        failures.append("Korean name must contain Hangul")

    indian = generate_profile_identity("in", seed="in-seed", today=today)
    if not indian.name.isascii():
        failures.append("Indian name must use Latin characters")

    names = {
        generate_profile_identity("vi", seed=f"job-{index}", today=today).name
        for index in range(32)
    }
    if len(names) < 20:
        failures.append(f"Vietnamese name pool is too repetitive: {len(names)}/32")

    try:
        generate_profile_identity("xx", seed="bad", today=today)
        failures.append("unknown region must fail fast")
    except ValueError:
        pass

    _check_job_persistence(failures)

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] profile identity" if failures else "[ok] profile identity")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
