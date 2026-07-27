"""Check mailbox-to-alias registration coordination without network calls."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path


class _FakeProvider:
    provider_id = "smsbower"

    def __init__(self):
        self.prepared: list[str] = []
        self.closed: list[tuple[str, bool]] = []

    def rent(self, *, product_id=None):
        from gpt_reg.mail.rental import MailRental

        return MailRental(
            provider=self.provider_id,
            external_id="mail-44",
            base_email="base@gmail.com",
            product_id=product_id,
            expires_at="2099-01-01T00:00:00Z",
            balance_after_rent=900,
        )

    def wait_for_otp(self, rental, *, alias, **_kwargs):
        return "123456"

    def prepare_next(self, rental):
        self.prepared.append(rental.external_id)
        return rental

    def close(self, rental, *, success):
        self.closed.append((rental.external_id, success))


def _fresh_repositories():
    from gpt_reg.db import JobRepository, MailRentalRepository, connect, migrate

    conn = connect(Path(tempfile.mkdtemp()) / "rental.db")
    migrate(conn)
    return conn, JobRepository(conn), MailRentalRepository(conn)


def _wait(predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _check_manager_integration(failures: list[str]) -> None:
    from gpt_reg.models import SignupResult
    from gpt_reg.web.jobs import reg_manager as manager_module

    conn, jobs, rentals = _fresh_repositories()
    manager = manager_module.RegJobManager()
    calls: list[tuple[str, object]] = []

    def fake_signup(request, *, mail=None, **_kwargs):
        calls.append((request.email, mail))
        if len(calls) == 1:
            return SignupResult(ok=True, email=request.email, outcome="success")
        return SignupResult(
            ok=False,
            email=request.email,
            outcome="account_exists",
            error="exists",
        )

    original = manager_module.run_signup
    manager_module.run_signup = fake_signup
    try:
        rental_ids = manager.start_rental_batch(
            rental_count=1,
            provider_factory=_FakeProvider,
            jobs_repo=jobs,
            rentals_repo=rentals,
            source="gmail_smsbower",
            product_id=None,
            alias_limit=5,
            profile_region="ko",
            headless=True,
            reg_mode="http",
            concurrency=1,
            balance_before=1000,
        )
        if len(rental_ids) != 1:
            failures.append(f"manager returned wrong rental ids: {rental_ids!r}")
        if not _wait(lambda: not manager.running):
            failures.append("rental manager did not return to idle")
            return
        if len(calls) != 2 or any(mail is None for _email, mail in calls):
            failures.append(f"manager did not pass rental mailbox override: {calls!r}")
        rows = jobs.list_recent()
        if len(rows) != 2 or {row["mail_mode"] for row in rows} != {"gmail_smsbower"}:
            failures.append(f"manager created wrong rental jobs: {rows!r}")
        rental = rentals.get(rental_ids[0]) if rental_ids else None
        if not rental or rental["status"] != "account_exists":
            failures.append(f"manager did not stop rental on account_exists: {rental!r}")
    finally:
        manager_module.run_signup = original
        conn.close()


def _check_signup_mail_override(failures: list[str]) -> None:
    from gpt_reg.config import Settings
    from gpt_reg.models import BrowserHandoff, SignupRequest
    from gpt_reg import signup

    root = Path(tempfile.mkdtemp())
    settings = Settings(root_dir=root, runtime_dir=root / "runtime")
    supplied_mail = object()
    seen_mail: list[object] = []

    class _Phase:
        mode = "http"

        async def run(self, ctx, request, mail, *, log):
            seen_mail.append(mail)
            return BrowserHandoff(
                access_token="access",
                authenticated_email=request.email,
                registration_outcome="account_exists",
            )

    async def fake_http_phase(**_kwargs):
        return {
            "access_token": "access",
            "session_token": "session",
            "authenticated_email": "base+alias@gmail.com",
            "cookies": [],
        }

    original_phase = signup.get_phase
    original_http = signup.run_http_phase
    original_save = signup.save_session_file
    previous_conn = signup._shared_conn
    previous_root = signup._shared_root
    signup.get_phase = lambda _mode: _Phase()
    signup.run_http_phase = fake_http_phase
    signup.save_session_file = lambda **_kwargs: root / "session.json"
    signup._shared_conn = None
    signup._shared_root = None
    try:
        result = signup.run_signup(
            SignupRequest(
                email="base+alias@gmail.com",
                mail_provider="gmail_smsbower",
                reg_mode="http",
            ),
            settings=settings,
            mail=supplied_mail,
        )
        if not result.ok or result.outcome != "account_exists":
            failures.append(f"run_signup did not preserve structured outcome: {result!r}")
        if seen_mail != [supplied_mail]:
            failures.append("run_signup did not use supplied rental mailbox provider")
    finally:
        if signup._shared_conn is not None:
            signup._shared_conn.close()
        signup._shared_conn = previous_conn
        signup._shared_root = previous_root
        signup.get_phase = original_phase
        signup.run_http_phase = original_http
        signup.save_session_file = original_save


def main() -> int:
    from gpt_reg.models import SignupResult
    from gpt_reg.web.jobs.rental_coordinator import RentalCoordinator

    failures: list[str] = []
    _check_signup_mail_override(failures)
    _check_manager_integration(failures)
    conn, jobs, rentals = _fresh_repositories()
    provider = _FakeProvider()
    outcomes = [
        SignupResult(ok=True, email="first@gmail.com", outcome="success"),
        SignupResult(ok=False, email="second@gmail.com", outcome="account_exists", error="exists"),
    ]
    aliases: list[str] = []
    mail_codes: list[str] = []

    def execute(row, mailbox):
        aliases.append(row["email"])
        mail_codes.append(
            mailbox.wait_for_otp(
                email=row["email"],
                since=None,
                timeout_s=1,
                poll_interval_s=0.01,
                log=lambda _line: None,
            )
        )
        return outcomes.pop(0)

    coordinator = RentalCoordinator()
    try:
        job_ids = coordinator.run_rental(
            rental_id="rental-1",
            provider=provider,
            rentals_repo=rentals,
            jobs_repo=jobs,
            source="gmail_smsbower",
            product_id=None,
            alias_limit=5,
            profile_region="vi",
            reg_mode="http",
            execute=execute,
            should_cancel=lambda: False,
            balance_before=1000,
        )
        if len(job_ids) != 2 or len(set(job_ids)) != 2:
            failures.append(f"coordinator created wrong job ids: {job_ids!r}")
        if len(aliases) != 2 or len(set(aliases)) != 2:
            failures.append(f"aliases are not unique: {aliases!r}")
        if not all(alias.startswith("base+") and alias.endswith("@gmail.com") for alias in aliases):
            failures.append(f"aliases have wrong format: {aliases!r}")
        if mail_codes != ["123456", "123456"]:
            failures.append(f"mail bridge did not poll provider: {mail_codes!r}")
        if provider.prepared != ["mail-44"]:
            failures.append(f"prepare_next calls are wrong: {provider.prepared!r}")
        if provider.closed != [("mail-44", True)]:
            failures.append(f"close calls are wrong: {provider.closed!r}")

        rental_row = rentals.get("rental-1")
        if not rental_row or rental_row["status"] != "account_exists":
            failures.append(f"rental did not stop on structured account_exists: {rental_row!r}")
        elif rental_row["alias_count"] != 2:
            failures.append(f"rental alias_count is wrong: {rental_row['alias_count']!r}")

        rows = [jobs.get(job_id) for job_id in job_ids]
        if any(not row or row.get("rental_id") != "rental-1" for row in rows):
            failures.append(f"jobs are not linked to rental: {rows!r}")
        if any(not row.get("profile_name") or not row.get("birthdate") for row in rows if row):
            failures.append("rental jobs did not persist profile identity")
        if any(not row.get("fingerprint_seed") for row in rows if row):
            failures.append("rental jobs did not persist fingerprint identity")
        if rows and rows[0] and rows[0].get("status") != "success":
            failures.append(f"successful alias job status is wrong: {rows[0]!r}")
        if len(rows) > 1 and rows[1] and rows[1].get("status") != "error":
            failures.append(f"account-exists alias job status is wrong: {rows[1]!r}")
    finally:
        conn.close()

    conn, jobs, rentals = _fresh_repositories()
    provider = _FakeProvider()
    try:
        limited = RentalCoordinator().run_rental(
            rental_id="rental-limit",
            provider=provider,
            rentals_repo=rentals,
            jobs_repo=jobs,
            source="gmail_accstack",
            product_id="5",
            alias_limit=1,
            profile_region="in",
            reg_mode="browser",
            execute=lambda row, mailbox: SignupResult(
                ok=True,
                email=row["email"],
                outcome="success",
            ),
            should_cancel=lambda: False,
            balance_before=500,
        )
        row = rentals.get("rental-limit")
        if len(limited) != 1 or not row or row["status"] != "limit_reached":
            failures.append(f"alias limit was not enforced: {limited!r} {row!r}")
        if provider.prepared:
            failures.append("provider prepared another code after final alias")
    finally:
        conn.close()

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] rental coordinator" if failures else "[ok] rental coordinator")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
