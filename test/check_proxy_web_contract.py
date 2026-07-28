"""Web proxy selection is always active and integration keys stay local/no-store."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

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
        self.rental_proxies: list[str | None] = []
        self.retry_proxies: list[str | None] = []

    def start_batch(self, **kwargs):
        self.outlook_calls.append(kwargs)
        return list(kwargs.get("job_ids") or ["job-fixture"])

    def start_rental_batch(self, **kwargs):
        self.rental_proxies.append(kwargs["provider_factory"]().proxy_url)
        return ["rental-fixture"]

    def start_rental_retry_batch(self, **kwargs):
        provider = kwargs["provider_factory"]("gmail_smsbower")
        self.retry_proxies.append(provider.proxy_url)
        return list(kwargs["job_ids"])


class _FakeCheckManager:
    running = False

    def __init__(self):
        self.calls: list[dict] = []

    def start_batch(self, **kwargs):
        self.calls.append(kwargs)
        return list(kwargs.get("check_ids") or ["check-fixture"])


class _CountingPool:
    def __init__(self):
        self.acquire_calls = 0

    def acquire_url(self):
        self.acquire_calls += 1
        return SELECTED_URL


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
        failures.append("primary/fallback did not reuse the selected proxy")


def main() -> int:
    from gpt_reg.db import (
        ChecksRepository,
        JobRepository,
        MailRentalRepository,
        ProxyRepository,
        SettingsRepository,
        connect,
        migrate,
    )
    from gpt_reg.db.repositories import MASKED_VALUE
    from gpt_reg.web import server

    failures: list[str] = []
    conn = connect(Path(tempfile.mkdtemp()) / "proxy-web.db")
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
        repos["settings_repo"].set("sms.smsbower.api_key", "sms-secret")
        repos["settings_repo"].set("accstack.api_key", "acc-secret")
        repos["proxy_repo"].replace_all(
            [
                {"value": "ignored.example:8201", "selected": False},
                {"value": SELECTED_PROXY, "selected": True},
            ]
        )
        client = TestClient(server.app)

        proxy_body = client.get("/api/proxies").json()
        if proxy_body.get("enabled") is not True or proxy_body.get("selected") != 1:
            failures.append(f"web proxy GET is not always enabled: {proxy_body!r}")

        status = client.get(
            "/api/mail-sources/status",
            params={"source": "gmail_smsbower", "proxy_enabled": "false"},
        )
        if status.status_code != 200 or provider_proxies[-1] != SELECTED_URL:
            failures.append("mail status honored legacy proxy_enabled=false")

        start = client.post(
            "/api/jobs/start",
            json={
                "source": "outlook",
                "input": OUTLOOK_COMBO,
                "proxy_enabled": False,
            },
        )
        pool = reg_manager.outlook_calls[-1].get("proxy_pool")
        if start.status_code != 200 or pool is None or pool.acquire_url() != SELECTED_URL:
            failures.append("Outlook start did not use selected SQLite proxy")

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
        retry = client.post(
            "/api/jobs/retry",
            json={"job_ids": ["outlook-retry"], "proxy_enabled": False},
        )
        retry_pool = reg_manager.outlook_calls[-1].get("proxy_pool")
        if retry.status_code != 200 or retry_pool.acquire_url() != SELECTED_URL:
            failures.append("Outlook retry did not use selected SQLite proxy")

        gmail = client.post(
            "/api/jobs/start",
            json={
                "source": "gmail_smsbower",
                "rental_count": 1,
                "product_id": "5",
                "proxy_enabled": False,
            },
        )
        if gmail.status_code != 200 or reg_manager.rental_proxies[-1] != SELECTED_URL:
            failures.append("Gmail start did not use selected SQLite proxy")

        check = client.post(
            "/api/checks/start",
            json={
                "input": "fixture@example.com|secret",
                "proxy_enabled": False,
            },
        )
        if check.status_code != 200 or check_manager.calls[-1].get("proxy_enabled") is not True:
            failures.append("check start was not forced to the selected proxy pool")

        legacy_disable = client.put(
            "/api/proxies",
            json={
                "enabled": False,
                "items": [
                    {"value": row["value"], "selected": row["selected"]}
                    for row in repos["proxy_repo"].list_all()
                ],
            },
        )
        if legacy_disable.status_code != 200 or legacy_disable.json().get("enabled") is not True:
            failures.append("legacy enabled=false disabled the web proxy pool")

        before = repos["proxy_repo"].list_all()
        all_off = client.put(
            "/api/proxies",
            json={
                "enabled": False,
                "items": [
                    {"value": row["value"], "selected": False}
                    for row in before
                ],
            },
        )
        if all_off.status_code != 400:
            failures.append("all-off proxy update was accepted")
        if repos["proxy_repo"].list_all() != before:
            failures.append("all-off proxy update was not atomic")

        keys = client.get("/api/settings/integration-keys")
        expected_keys = {
            "sms.smsbower.api_key": "sms-secret",
            "accstack.api_key": "acc-secret",
        }
        if keys.status_code != 200 or keys.json() != expected_keys:
            failures.append(f"integration key response is wrong: {keys.text}")
        if keys.headers.get("cache-control") != "no-store":
            failures.append("integration key response is cacheable")
        masked = client.get("/api/settings").json()
        if any(masked.get(name) != MASKED_VALUE for name in expected_keys):
            failures.append("generic settings endpoint exposed an integration key")

        repos["proxy_repo"].replace_all(
            [{"value": SELECTED_PROXY, "selected": False}]
        )
        for path, payload in (
            ("/api/jobs/start", {"source": "outlook", "input": OUTLOOK_COMBO}),
            ("/api/checks/start", {"input": "fixture@example.com|secret"}),
        ):
            response = client.post(path, json=payload)
            if response.status_code != 400:
                failures.append(f"all-off runtime did not fail fast: {path}")
        response = client.get(
            "/api/mail-sources/status",
            params={"source": "gmail_smsbower"},
        )
        if response.status_code != 400:
            failures.append("all-off mail status did not fail fast")
    finally:
        for name, value in patched.items():
            setattr(server, name, value)
        conn.close()

    _check_fallback_reuses_one_proxy(failures)
    for line in failures:
        print(f"[fail] {line}")
    print("[fail] web proxy contract" if failures else "[ok] web proxy contract")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
