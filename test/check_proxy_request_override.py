"""Per-request proxy override must reach every registration/check execution path."""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


SELECTED_PROXY = "selected.example:8202"
SELECTED_URL = f"http://{SELECTED_PROXY}"
OUTLOOK_COMBO = (
    "fixture@hotmail.com|Passw0rd|refresh-token|"
    "12345678-1234-1234-1234-123456789abc"
)


class _FakeProvider:
    provider_id = "fixture"

    def __init__(self, source: str, proxy_url: str | None):
        self.source = source
        self.proxy_url = proxy_url

    def status(self):
        from gpt_reg.mail.rental import MailProduct, MailSourceStatus

        product = MailProduct(id="5", name="Gmail ChatGPT", price=50, stock=10)
        return MailSourceStatus(
            configured=True,
            balance=500,
            currency="USD",
            price=50,
            stock=10,
            affordable=10,
            products=(product,),
        )


class _FakeRegManager:
    running = False

    def __init__(self):
        self.outlook_calls: list[dict] = []
        self.rental_calls: list[dict] = []
        self.rental_retry_calls: list[dict] = []

    def start_batch(self, **kwargs):
        self.outlook_calls.append(kwargs)
        return list(kwargs.get("job_ids") or ["job-fixture"])

    def start_rental_batch(self, **kwargs):
        provider = kwargs["provider_factory"]()
        self.rental_calls.append({**kwargs, "provider_proxy": provider.proxy_url})
        return ["rental-fixture"]

    def start_rental_retry_batch(self, **kwargs):
        provider = kwargs["provider_factory"]("gmail_smsbower")
        self.rental_retry_calls.append({**kwargs, "provider_proxy": provider.proxy_url})
        return list(kwargs["job_ids"])


class _FakeCheckManager:
    running = False

    def __init__(self):
        self.calls: list[dict] = []

    def start_batch(self, **kwargs):
        self.calls.append(kwargs)
        return list(kwargs.get("check_ids") or ["check-fixture"])


def _pool_url(call: dict) -> str | None:
    pool = call.get("proxy_pool")
    return pool.acquire_url() if pool is not None else None


def _check_api_overrides(failures: list[str]) -> None:
    from gpt_reg.db import (
        ChecksRepository,
        JobRepository,
        MailRentalRepository,
        ProxyRepository,
        SettingsRepository,
        connect,
        migrate,
    )
    from gpt_reg.web import server

    conn = connect(Path(tempfile.mkdtemp()) / "proxy-request.db")
    migrate(conn)
    repos = {
        "settings_repo": SettingsRepository(conn),
        "jobs_repo": JobRepository(conn),
        "checks_repo": ChecksRepository(conn),
        "rentals_repo": MailRentalRepository(conn),
        "proxy_repo": ProxyRepository(conn),
    }
    reg_manager = _FakeRegManager()
    check_manager = _FakeCheckManager()
    provider_proxies: list[str | None] = []
    patched = {
        **{name: getattr(server, name) for name in repos},
        "reg_manager": server.reg_manager,
        "check_manager": server.check_manager,
        "_provider_for_source": server._provider_for_source,
    }
    try:
        for name, repo in repos.items():
            setattr(server, name, repo)
        server.reg_manager = reg_manager
        server.check_manager = check_manager
        def fake_provider(source, proxy_url=None):
            provider_proxies.append(proxy_url)
            return _FakeProvider(source, proxy_url)

        server._provider_for_source = fake_provider
        repos["settings_repo"].set("proxy.enabled", "false")
        repos["settings_repo"].set("sms.smsbower.api_key", "fixture-key")
        repos["proxy_repo"].replace_all(
            [
                {"value": "ignored.example:8201", "selected": False},
                {"value": SELECTED_PROXY, "selected": True},
            ]
        )
        client = TestClient(server.app)

        repos["settings_repo"].set("proxy.enabled", "true")
        response = client.get(
            "/api/mail-sources/status",
            params={"source": "gmail_smsbower", "proxy_enabled": "false"},
        )
        if response.status_code != 200 or provider_proxies[-1] is not None:
            failures.append("mail status proxy_enabled=false did not force direct")
        repos["settings_repo"].set("proxy.enabled", "false")
        response = client.get(
            "/api/mail-sources/status",
            params={"source": "gmail_smsbower", "proxy_enabled": "true"},
        )
        if response.status_code != 200 or provider_proxies[-1] != SELECTED_URL:
            failures.append("mail status did not enable the selected request proxy")

        response = client.post(
            "/api/jobs/start",
            json={"source": "outlook", "input": OUTLOOK_COMBO, "proxy_enabled": True},
        )
        if response.status_code != 200 or _pool_url(reg_manager.outlook_calls[-1]) != SELECTED_URL:
            failures.append("jobs/start did not enable the selected request proxy")

        repos["settings_repo"].set("proxy.enabled", "true")
        response = client.post(
            "/api/jobs/start",
            json={"source": "outlook", "input": OUTLOOK_COMBO, "proxy_enabled": False},
        )
        if response.status_code != 200 or _pool_url(reg_manager.outlook_calls[-1]) is not None:
            failures.append("jobs/start proxy_enabled=false did not force direct")

        response = client.post(
            "/api/jobs/start",
            json={"source": "outlook", "input": OUTLOOK_COMBO},
        )
        if response.status_code != 200 or _pool_url(reg_manager.outlook_calls[-1]) != SELECTED_URL:
            failures.append("jobs/start missing override did not use SQLite setting")

        repos["jobs_repo"].create(
            {
                "id": "outlook-retry",
                "email": "fixture@hotmail.com",
                "combo": OUTLOOK_COMBO,
                "mail_mode": "outlook",
                "reg_mode": "http",
                "status": "error",
                "created_at": time.time(),
            }
        )
        response = client.post(
            "/api/jobs/retry",
            json={"job_ids": ["outlook-retry"], "proxy_enabled": False},
        )
        if response.status_code != 200 or _pool_url(reg_manager.outlook_calls[-1]) is not None:
            failures.append("jobs/retry proxy_enabled=false did not force direct")

        response = client.post(
            "/api/jobs/start",
            json={
                "source": "gmail_smsbower",
                "rental_count": 1,
                "product_id": "5",
                "proxy_enabled": False,
            },
        )
        if response.status_code != 200 or reg_manager.rental_calls[-1]["provider_proxy"] is not None:
            failures.append("Gmail start proxy_enabled=false did not force direct")

        repos["settings_repo"].set("proxy.enabled", "false")
        response = client.post(
            "/api/jobs/start",
            json={
                "source": "gmail_smsbower",
                "rental_count": 1,
                "product_id": "5",
                "proxy_enabled": True,
            },
        )
        if response.status_code != 200 or reg_manager.rental_calls[-1]["provider_proxy"] != SELECTED_URL:
            failures.append("Gmail start did not enable the selected request proxy")

        repos["rentals_repo"].create(
            {
                "id": "gmail-rental",
                "provider": "fixture",
                "external_id": "external-fixture",
                "base_email": "fixture@gmail.com",
                "status": "error",
                "created_at": time.time(),
            }
        )
        repos["jobs_repo"].create(
            {
                "id": "gmail-retry",
                "email": "fixture+alias@gmail.com",
                "combo": "fixture+alias@gmail.com",
                "mail_mode": "gmail_smsbower",
                "reg_mode": "http",
                "status": "error",
                "rental_id": "gmail-rental",
                "source_email": "fixture@gmail.com",
                "created_at": time.time(),
            }
        )
        response = client.post(
            "/api/jobs/retry",
            json={"job_ids": ["gmail-retry"], "proxy_enabled": True},
        )
        if response.status_code != 200 or reg_manager.rental_retry_calls[-1]["provider_proxy"] != SELECTED_URL:
            failures.append("Gmail retry did not enable the selected request proxy")

        response = client.post(
            "/api/checks/start",
            json={"input": "fixture@example.com|secret", "proxy_enabled": True},
        )
        if response.status_code != 200 or check_manager.calls[-1].get("proxy_enabled") is not True:
            failures.append("checks/start did not forward proxy_enabled=true")

        repos["settings_repo"].set("proxy.enabled", "true")
        repos["checks_repo"].create(
            {
                "id": "check-retry",
                "email": "fixture@example.com",
                "combo": "fixture@example.com|secret",
                "status": "error",
                "created_at": time.time(),
            }
        )
        response = client.post(
            "/api/checks/retry",
            json={"check_ids": ["check-retry"], "proxy_enabled": False},
        )
        if response.status_code != 200 or check_manager.calls[-1].get("proxy_enabled") is not False:
            failures.append("checks/retry proxy_enabled=false did not force direct")

        repos["proxy_repo"].replace_all([])
        repos["settings_repo"].set("proxy.enabled", "false")
        response = client.post(
            "/api/jobs/start",
            json={"source": "outlook", "input": OUTLOOK_COMBO, "proxy_enabled": True},
        )
        if response.status_code != 400:
            failures.append("request-enabled empty proxy pool did not fail fast")
        response = client.post(
            "/api/checks/start",
            json={"input": "fixture@example.com|secret", "proxy_enabled": True},
        )
        if response.status_code != 400:
            failures.append("check-enabled empty proxy pool did not fail fast")
    finally:
        for name, value in patched.items():
            setattr(server, name, value)
        conn.close()


class _CountingPool:
    def __init__(self):
        self.acquire_calls = 0

    def acquire_url(self):
        self.acquire_calls += 1
        return SELECTED_URL

    def acquire(self):
        self.acquire_calls += 1
        return {"server": SELECTED_URL}


def _check_direct_phase_guards(failures: list[str]) -> None:
    from gpt_reg.models import SignupRequest
    from gpt_reg.signup import _resolve_proxy_url

    request = SignupRequest(
        email="fixture@example.com",
        proxy=SELECTED_URL,
        proxy_enabled=False,
    )
    resolve_pool = _CountingPool()
    if _resolve_proxy_url(request, SimpleNamespace(proxy_pool=resolve_pool)) is not None:
        failures.append("signup proxy resolver ignored explicit direct mode")
    if resolve_pool.acquire_calls:
        failures.append("signup proxy resolver touched the global pool in direct mode")

    from gpt_reg.phases import http_reg
    from gpt_reg.sentinel import pool as sentinel_pool

    http_pool = _CountingPool()
    captured: dict[str, object] = {}

    class _SentinelPool:
        def acquire(self, _log):
            return None

        def release(self, _worker):
            return None

    original_get_pool = sentinel_pool.get_pool
    original_run_sync = http_reg._run_sync
    sentinel_pool.get_pool = lambda: _SentinelPool()

    def fake_run_sync(_ctx, current_request, _mail, _worker, _log):
        captured["request"] = current_request
        return {"cookies": []}

    http_reg._run_sync = fake_run_sync
    try:
        asyncio.run(
            http_reg.HttpRegPhase().run(
                SimpleNamespace(proxy_pool=http_pool, should_cancel=None),
                request.model_copy(update={"reg_mode": "http"}),
                object(),
                log=lambda _line: None,
            )
        )
    finally:
        sentinel_pool.get_pool = original_get_pool
        http_reg._run_sync = original_run_sync
    if http_pool.acquire_calls or getattr(captured.get("request"), "proxy", None):
        failures.append("HTTP phase reacquired a global proxy in direct mode")

    http_pool_without_url = _CountingPool()
    captured.clear()
    sentinel_pool.get_pool = lambda: _SentinelPool()
    http_reg._run_sync = fake_run_sync
    try:
        asyncio.run(
            http_reg.HttpRegPhase().run(
                SimpleNamespace(proxy_pool=http_pool_without_url, should_cancel=None),
                request.model_copy(update={"reg_mode": "http", "proxy": None}),
                object(),
                log=lambda _line: None,
            )
        )
    finally:
        sentinel_pool.get_pool = original_get_pool
        http_reg._run_sync = original_run_sync
    if http_pool_without_url.acquire_calls or getattr(captured.get("request"), "proxy", None):
        failures.append("HTTP phase reacquired a global proxy without a request URL")

    from gpt_reg.phases import browser

    browser_pool = _CountingPool()
    launch: dict[str, object] = {}

    class _StopBrowser(Exception):
        pass

    original_camoufox = browser.AsyncCamoufox
    original_materialize = browser.materialize_browser_fingerprint
    original_launch_identity = browser.browser_launch_identity

    def stop_camoufox(**kwargs):
        launch.update(kwargs)
        raise _StopBrowser

    browser.AsyncCamoufox = stop_camoufox
    browser.materialize_browser_fingerprint = lambda _seed: object()
    browser.browser_launch_identity = lambda _fingerprint, **_kwargs: ({}, {})
    root = Path(tempfile.mkdtemp())
    try:
        try:
            asyncio.run(
                browser.BrowserPhase().run(
                    SimpleNamespace(
                        proxy_pool=browser_pool,
                        settings=SimpleNamespace(
                            profiles_dir=root,
                            browser_locale="en-US",
                            browser_geoip=True,
                        ),
                    ),
                    request.model_copy(update={"reg_mode": "browser"}),
                    object(),
                    log=lambda _line: None,
                )
            )
        except _StopBrowser:
            pass
    finally:
        browser.AsyncCamoufox = original_camoufox
        browser.materialize_browser_fingerprint = original_materialize
        browser.browser_launch_identity = original_launch_identity
    if browser_pool.acquire_calls or launch.get("proxy") is not None:
        failures.append("Browser phase reacquired a global proxy in direct mode")


def _check_fallback_reuses_one_proxy(failures: list[str]) -> None:
    from gpt_reg.models import SignupResult
    from gpt_reg.web.jobs.reg_manager import RegJobManager

    class _Repo:
        def __init__(self):
            self.row = {"id": "fallback-job", "email": "fixture@example.com"}

        def update(self, _job_id, **fields):
            self.row.update(fields)

        def append_log(self, _job_id, _line):
            return None

        def get(self, _job_id):
            return dict(self.row)

    manager = RegJobManager()
    repo = _Repo()
    pool = _CountingPool()
    attempts: list[dict] = []

    def attempt(_repo, row, _job_id, **kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            return SignupResult(
                ok=False,
                email=str(row["email"]),
                error="primary failed",
                fallback_eligible=True,
            )
        return SignupResult(ok=True, email=str(row["email"]), outcome="success")

    manager._attempt_signup = attempt
    manager._run_one(
        repo,
        dict(repo.row),
        headless=True,
        with_2fa=False,
        reg_mode="http",
        fallback_enabled=True,
        proxy_pool=pool,
    )
    if pool.acquire_calls != 1:
        failures.append(f"fallback acquired {pool.acquire_calls} proxies instead of one")
    if [item.get("reg_mode") for item in attempts] != ["http", "browser"]:
        failures.append(f"fallback modes are wrong: {attempts!r}")
    if any(
        item.get("proxy_url") != SELECTED_URL or item.get("proxy_enabled") is not True
        for item in attempts
    ):
        failures.append("primary/fallback did not reuse the selected request proxy")


def main() -> int:
    failures: list[str] = []
    _check_api_overrides(failures)
    _check_direct_phase_guards(failures)
    _check_fallback_reuses_one_proxy(failures)
    for line in failures:
        print(f"[fail] {line}")
    print("[fail] request proxy override" if failures else "[ok] request proxy override")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
