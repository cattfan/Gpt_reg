"""Check Gmail retry reuses an existing rental without billable operations."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path


class _ExistingRentalProvider:
    provider_id = "smsbower"

    def __init__(self) -> None:
        self.waited_aliases: list[str] = []
        self.billable_calls: list[str] = []

    def rent(self, *, product_id=None):
        del product_id
        self.billable_calls.append("rent")
        raise AssertionError("retry must not rent another mailbox")

    def prepare_next(self, rental):
        del rental
        self.billable_calls.append("prepare_next")
        raise AssertionError("retry must not rerent the mailbox")

    def close(self, rental, *, success):
        del rental, success
        self.billable_calls.append("close")
        raise AssertionError("retry must not mutate the rental lifecycle")

    def wait_for_otp(self, rental, *, alias, **_kwargs):
        if rental.external_id != "rental-external-1":
            raise AssertionError("retry rebuilt the wrong rental")
        self.waited_aliases.append(alias)
        return "123456"


def _repositories():
    from gpt_reg.db import JobRepository, MailRentalRepository, connect, migrate

    conn = connect(Path(tempfile.mkdtemp()) / "rental-retry.db")
    migrate(conn)
    return conn, JobRepository(conn), MailRentalRepository(conn)


def _wait(predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _create_rental(rentals, *, rental_id: str, expires_at: str | None) -> None:
    rentals.create(
        {
            "id": rental_id,
            "provider": "smsbower",
            "external_id": "rental-external-1",
            "base_email": "base@gmail.com",
            "product_id": "dr:gmail.com",
            "status": "error",
            "expires_at": expires_at,
            "balance_before": 100,
            "balance_after_rent": 99,
            "alias_count": 1,
            "created_at": time.time(),
        }
    )


def _create_job(jobs, *, job_id: str, rental_id: str | None) -> None:
    from gpt_reg.fingerprint import new_seed, profile_for_seed

    fingerprint_seed = new_seed()
    jobs.create(
        {
            "id": job_id,
            "email": "base+alias1@gmail.com",
            "combo": "not-an-outlook-combo",
            "mail_mode": "gmail_smsbower",
            "reg_mode": "http",
            "status": "error",
            "error": "temporary registration error",
            "password": None,
            "session_path": None,
            "fingerprint_seed": fingerprint_seed,
            "fingerprint_profile": profile_for_seed(fingerprint_seed).name,
            "fingerprint_data": None,
            "rental_id": rental_id,
            "source_email": "base@gmail.com",
            "alias_index": 1,
            "profile_region": "ko",
            "profile_name": "Kim Min-jun",
            "birthdate": "1997-05-12",
            "created_at": time.time(),
            "started_at": None,
            "finished_at": time.time(),
        }
    )


def _check_reuses_existing_rental(failures: list[str]) -> None:
    from gpt_reg.models import SignupResult
    from gpt_reg.web.jobs import reg_manager as manager_module

    conn, jobs, rentals = _repositories()
    _create_rental(rentals, rental_id="rental-1", expires_at="2099-01-01T00:00:00Z")
    _create_job(jobs, job_id="gmail-job-1", rental_id="rental-1")
    original = jobs.get("gmail-job-1")
    jobs.append_log("gmail-job-1", "old retry log")
    provider = _ExistingRentalProvider()
    factory_sources: list[str] = []
    signup_calls: list[object] = []

    def provider_factory(source: str):
        factory_sources.append(source)
        return provider

    def fake_signup(request, *, mail=None, log, **_kwargs):
        signup_calls.append((request, mail))
        log("[retry] existing rental")
        code = mail.wait_for_otp(
            email=request.email,
            since=None,
            timeout_s=1,
            poll_interval_s=0.01,
            log=log,
        )
        if code != "123456":
            raise AssertionError("wrong OTP")
        return SignupResult(
            ok=True,
            email=request.email,
            password="chatgpt-password",
            outcome="success",
        )

    previous_signup = manager_module.run_signup
    manager_module.run_signup = fake_signup
    manager = manager_module.RegJobManager()
    try:
        ids = manager.start_rental_retry_batch(
            job_ids=["gmail-job-1"],
            provider_factory=provider_factory,
            jobs_repo=jobs,
            rentals_repo=rentals,
            headless=True,
            with_2fa=False,
            reg_mode="http",
            fallback_enabled=False,
            concurrency=1,
        )
        if ids != ["gmail-job-1"]:
            failures.append(f"retry changed job ids: {ids!r}")
        if not _wait(lambda: not manager.running):
            failures.append("rental retry manager did not return to idle")
            return

        current = jobs.get("gmail-job-1") or {}
        if current.get("status") != "success":
            failures.append(f"rental retry did not finish successfully: {current!r}")
        if current.get("profile_name") != original.get("profile_name"):
            failures.append("rental retry changed the saved profile")
        if current.get("fingerprint_seed") != original.get("fingerprint_seed"):
            failures.append("rental retry changed the fingerprint identity")
        logs = jobs.logs("gmail-job-1")
        if "old retry log" in logs or "[retry] existing rental" not in logs:
            failures.append(f"rental retry log lifecycle is wrong: {logs!r}")
        if factory_sources != ["gmail_smsbower"]:
            failures.append(f"provider factory received wrong source: {factory_sources!r}")
        if len(signup_calls) != 1:
            failures.append(f"retry made {len(signup_calls)} signup attempts")
        else:
            request, mailbox = signup_calls[0]
            if request.email != "base+alias1@gmail.com" or request.outlook_combo is not None:
                failures.append("Gmail retry was parsed as an Outlook combo")
            if request.name != "Kim Min-jun" or request.birthdate != "1997-05-12":
                failures.append("Gmail retry did not reuse profile identity")
            if mailbox is None:
                failures.append("Gmail retry did not provide a rental mailbox bridge")
        if provider.waited_aliases != ["base+alias1@gmail.com"]:
            failures.append(f"retry polled the wrong alias: {provider.waited_aliases!r}")
        if provider.billable_calls:
            failures.append(f"retry made billable/lifecycle calls: {provider.billable_calls!r}")
    finally:
        manager_module.run_signup = previous_signup
        conn.close()


def _check_invalid_rentals_fail_before_mutation(failures: list[str]) -> None:
    from gpt_reg.web.jobs.reg_manager import RegJobManager

    for rental_id, expires_at, expected in (
        (None, None, "missing"),
        ("expired-rental", "2000-01-01T00:00:00Z", "expired"),
    ):
        conn, jobs, rentals = _repositories()
        if expected == "expired":
            _create_rental(rentals, rental_id=rental_id, expires_at=expires_at)
        job_id = f"job-{expected}"
        _create_job(jobs, job_id=job_id, rental_id=rental_id)
        jobs.append_log(job_id, "must survive validation")
        provider_calls: list[str] = []
        try:
            try:
                RegJobManager().start_rental_retry_batch(
                    job_ids=[job_id],
                    provider_factory=lambda source: provider_calls.append(source),
                    jobs_repo=jobs,
                    rentals_repo=rentals,
                    headless=True,
                    reg_mode="http",
                    concurrency=1,
                )
            except ValueError as exc:
                if expected not in str(exc).lower():
                    failures.append(f"wrong {expected} rental error: {exc}")
            else:
                failures.append(f"{expected} rental did not fail fast")
            row = jobs.get(job_id) or {}
            if row.get("status") != "error" or jobs.logs(job_id) != ["must survive validation"]:
                failures.append(f"{expected} rental mutated job before validation")
            if provider_calls:
                failures.append(f"{expected} rental constructed provider: {provider_calls!r}")
        finally:
            conn.close()


def main() -> int:
    failures: list[str] = []
    _check_reuses_existing_rental(failures)
    _check_invalid_rentals_fail_before_mutation(failures)
    for failure in failures:
        print(f"[fail] {failure}")
    print("[fail] rental retry" if failures else "[ok] rental retry")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
