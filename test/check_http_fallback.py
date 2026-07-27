"""Fallback engine phải opt-in, đối xứng và không che lỗi engine chính."""

from __future__ import annotations

from types import SimpleNamespace

from gpt_reg.web.jobs import reg_manager as rm


class _FakeRepo:
    def __init__(self, row):
        self._row = dict(row)
        self.updates = []
        self.logs: list[str] = []

    def get(self, job_id):
        return dict(self._row)

    def update(self, job_id, **fields):
        self._row.update(fields)
        self.updates.append(fields)

    def append_log(self, job_id, line):
        self.logs.append(line)


def _result(ok, error=None, *, fallback_eligible=False):
    return SimpleNamespace(
        ok=ok, error=error, password="pw", session_path=None, mfa_activated=False,
        browser_seconds=None, http_seconds=None, mfa_seconds=None,
        fallback_eligible=fallback_eligible,
    )


def _make_manager(attempt_results):
    mgr = rm.RegJobManager.__new__(rm.RegJobManager)
    mgr._lock = __import__("threading").Lock()
    mgr._running_jobs = set()
    mgr._cancelled_jobs = set()
    mgr._emit = lambda *_a, **_k: None
    calls = []
    seq = list(attempt_results)

    def fake_attempt(jobs_repo, row, job_id, *, headless, with_2fa, reg_mode, log):
        calls.append(reg_mode)
        return seq.pop(0)

    mgr._attempt_signup = fake_attempt
    finished = {}
    mgr._finish_job = lambda jobs_repo, job_id, *, status, **f: finished.update(
        {"status": status, **f}
    )
    return mgr, calls, finished


def _run(primary: str, results, *, fallback_enabled: bool, cancelled: bool = False):
    row = {"id": "job1", "combo": "x@hotmail.com|p|r|c", "password": None}
    repo = _FakeRepo(row)
    mgr, calls, finished = _make_manager(results)
    mgr._should_cancel = lambda _j: cancelled
    mgr._run_one(
        repo, dict(row), headless=True, with_2fa=False, reg_mode=primary,
        fallback_enabled=fallback_enabled,
    )
    return repo, calls, finished


def _check_attempt_identity(failures: list[str]) -> None:
    from gpt_reg.browser.fingerprint import materialize_browser_fingerprint as real_materialize
    from gpt_reg.fingerprint import get_profile, profile_for_seed

    seed = "73" * 16
    profile = profile_for_seed(seed)
    row = {
        "id": "identity-job",
        "combo": "identity@hotmail.com|Passw0rd|refresh|12345678-1234-1234-1234-123456789abc",
        "password": None,
        "fingerprint_seed": seed,
        "fingerprint_profile": profile.name,
        "fingerprint_data": None,
    }

    class _IdentityRepo:
        def __init__(self):
            self.data = None

        def ensure_fingerprint_identity(self, job_id):
            return {
                "fingerprint_seed": seed,
                "fingerprint_profile": profile.name,
                "fingerprint_data": self.data,
            }

        def set_fingerprint_data_if_empty(self, job_id, payload):
            if self.data is None:
                self.data = payload
            return self.data

        def update(self, job_id, **fields):
            row.update(fields)

    repo = _IdentityRepo()
    manager = rm.RegJobManager()
    requests = []
    logs: list[str] = []
    materialize_calls = {"count": 0}

    def fake_run_signup(request, **_kwargs):
        requests.append(request)
        return _result(True)

    def fake_materialize(value):
        materialize_calls["count"] += 1
        return real_materialize(value)

    original_run = rm.run_signup
    had_materialize = hasattr(rm, "materialize_browser_fingerprint")
    original_materialize = getattr(rm, "materialize_browser_fingerprint", None)
    rm.run_signup = fake_run_signup
    rm.materialize_browser_fingerprint = fake_materialize
    try:
        manager._attempt_signup(
            repo, row, row["id"], headless=True, with_2fa=False,
            reg_mode="http", log=logs.append,
        )
        manager._attempt_signup(
            repo, row, row["id"], headless=True, with_2fa=False,
            reg_mode="browser", log=logs.append,
        )
        manager._attempt_signup(
            repo, row, row["id"], headless=True, with_2fa=False,
            reg_mode="browser", log=logs.append,
        )
    finally:
        rm.run_signup = original_run
        if had_materialize:
            rm.materialize_browser_fingerprint = original_materialize
        else:
            delattr(rm, "materialize_browser_fingerprint")

    if materialize_calls["count"] != 1:
        failures.append(f"Browser fingerprint materialize {materialize_calls['count']} lan")
    if len(requests) != 3:
        failures.append(f"attempt tao {len(requests)} request, can 3")
        return
    if requests[0].browser_fingerprint is not None:
        failures.append("HTTP attempt da materialize Browser fingerprint")
    if requests[1].browser_fingerprint != requests[2].browser_fingerprint:
        failures.append("Browser retry khong tai su dung fingerprint_data")
    for request in requests:
        if request.fingerprint_seed != seed or request.fingerprint_profile != profile.name:
            failures.append("retry/fallback da doi fingerprint identity")
            break
        canonical = get_profile(request.fingerprint_profile)
        if request.user_agent != canonical.user_agent or request.impersonate != canonical.impersonate:
            failures.append("SignupRequest khong dung canonical HTTP profile")
            break
    if seed in "\n".join(logs):
        failures.append("log fingerprint lam lo seed")
    if not any("[fingerprint] identity=" in line for line in logs):
        failures.append("attempt khong log public fingerprint identity")


def _check_log_redaction(failures: list[str]) -> None:
    row = {"id": "log-job", "combo": "x@hotmail.com|p|r|c", "password": None}
    repo = _FakeRepo(row)
    manager = rm.RegJobManager()
    manager._should_cancel = lambda _job_id: False
    manager._finish_job = lambda *_args, **_kwargs: None
    events: list[dict] = []
    manager.subscribe(events.append)

    def fake_attempt(jobs_repo, current, job_id, *, headless, with_2fa, reg_mode, log):
        log("[mail] OTP 123456 (verification, attempt 1)")
        log("[mfa] POST activate_enrollment factor_id=factor-secret code=654321")
        log('upstream {"access_token":"access-secret","refresh_token":"refresh-secret"}')
        log("OTP code: 777777")
        log("url=https://example.test/callback?code=query-secret&state=x")
        log("screenshot=C:\\private\\shot.png")
        log("[signup] session saved C:\\private\\session.json")
        return _result(False, "mail failed")

    manager._attempt_signup = fake_attempt
    manager._run_one(
        repo,
        dict(row),
        headless=True,
        with_2fa=False,
        reg_mode="http",
        fallback_enabled=False,
    )
    event_lines = [str(event.get("line", "")) for event in events if event.get("type") == "log"]
    exposed = "\n".join([*repo.logs, *event_lines])
    for secret in (
        "123456",
        "654321",
        "777777",
        "factor-secret",
        "access-secret",
        "refresh-secret",
        "query-secret",
        "C:\\private\\shot.png",
        "C:\\private\\session.json",
    ):
        if secret in exposed:
            failures.append(f"job log/SSE lam lo secret: {secret}")


def _check_failure_classification(failures: list[str]) -> None:
    from gpt_reg.core.exceptions import BrowserPhaseError, HttpRegError
    from gpt_reg.signup import _fallback_eligible_error
    from playwright.async_api import Error as PlaywrightError

    cases = (
        (HttpRegError("blocked", step="cf_block"), None, True),
        (BrowserPhaseError("stuck", step="turnstile"), None, True),
        (PlaywrightError("browser closed"), "browser", True),
        (PlaywrightError("browser closed"), "http", False),
        (HttpRegError("bad otp", step="verify"), None, False),
        (BrowserPhaseError("bad password", step="login"), None, False),
        (TimeoutError("mail timeout"), "browser", False),
    )
    for error, reg_mode, expected in cases:
        actual = _fallback_eligible_error(error, reg_mode=reg_mode)
        if actual is not expected:
            failures.append(
                f"phan loai fallback sai: {type(error).__name__}/{getattr(error, 'step', None)}"
            )


def main() -> int:
    failures: list[str] = []

    _check_attempt_identity(failures)
    _check_log_redaction(failures)
    _check_failure_classification(failures)

    # Mặc định contract của manager phải là không fallback.
    try:
        _, calls, finished = _run("http", [_result(False, "http failed")], fallback_enabled=False)
        if calls != ["http"] or finished.get("status") != "error":
            failures.append(f"fallback tắt nhưng calls={calls}, status={finished.get('status')}")
    except TypeError as exc:
        failures.append(f"_run_one chưa nhận fallback_enabled: {exc}")

    cases = (
        ("http", ["http", "browser"]),
        ("browser", ["browser", "http"]),
    )
    for primary, wanted in cases:
        try:
            repo, calls, finished = _run(
                primary,
                [
                    _result(False, f"{primary} failed", fallback_eligible=True),
                    _result(True),
                ],
                fallback_enabled=True,
            )
            if calls != wanted:
                failures.append(f"fallback {primary} sai: calls={calls}, muốn={wanted}")
            if finished.get("status") != "success":
                failures.append(f"fallback {primary} thành công nhưng status={finished.get('status')}")
            fallback = wanted[1]
            if not any(f"fallback={fallback}" in line for line in repo.logs):
                failures.append(f"log thiếu fallback={fallback}: {repo.logs}")
        except TypeError:
            # Signature đã được báo ở case mặc định; không nhân đôi cùng lỗi.
            pass

    try:
        _, calls, _ = _run("http", [_result(True)], fallback_enabled=True)
        if calls != ["http"]:
            failures.append(f"engine chính OK vẫn fallback: calls={calls}")

        _, calls, _ = _run(
            "browser", [_result(False, "cancelled")], fallback_enabled=True, cancelled=True
        )
        if calls != ["browser"]:
            failures.append(f"job bị huỷ vẫn fallback: calls={calls}")
    except TypeError:
        pass

    for primary, error in (("http", "mail refresh failed"), ("browser", "auth failed")):
        try:
            _, calls, finished = _run(
                primary,
                [_result(False, error, fallback_eligible=False)],
                fallback_enabled=True,
            )
            if calls != [primary] or finished.get("status") != "error":
                failures.append(
                    f"loi auth/mail bi fallback: primary={primary}, calls={calls}"
                )
        except (IndexError, TypeError):
            failures.append(f"loi auth/mail bi fallback: primary={primary}")

    if hasattr(rm, "_AUTO_FALLBACK_HTTP_TO_BROWSER"):
        failures.append("vẫn còn fallback HTTP -> Browser mặc định bằng biến môi trường")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] engine fallback" if failures else "[ok] engine fallback")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
