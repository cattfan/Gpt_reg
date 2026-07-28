"""Offline FastAPI checks for registration sources, proxies and check logs."""

from __future__ import annotations

import tempfile
import time
import sys
from pathlib import Path

from fastapi.testclient import TestClient

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


class _FakeManager:
    running = False

    def __init__(self):
        self.outlook_calls: list[dict] = []
        self.rental_calls: list[dict] = []
        self.rental_retry_calls: list[dict] = []

    def start_batch(self, **kwargs):
        self.outlook_calls.append(kwargs)
        return ["job-1"]

    def start_rental_batch(self, **kwargs):
        self.rental_calls.append(kwargs)
        count = kwargs["rental_count"]
        return [f"rental-{index}" for index in range(count)]

    def start_rental_retry_batch(self, **kwargs):
        self.rental_retry_calls.append(kwargs)
        return list(kwargs["job_ids"])


class _FakeProvider:
    provider_id = "fixture"

    def __init__(self, source: str):
        self.source = source

    def status(self):
        from gpt_reg.mail.rental import MailProduct, MailSourceStatus

        product = MailProduct(
            id="5" if self.source == "gmail_accstack" else "dr:gmail.com",
            name="Gmail OpenAI",
            price=50,
            stock=10,
        )
        return MailSourceStatus(
            configured=True,
            balance=500,
            currency="USD",
            price=50,
            stock=10,
            affordable=10,
            products=(product,),
        )


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
    from gpt_reg.web import server

    failures: list[str] = []
    conn = connect(Path(tempfile.mkdtemp()) / "operations.db")
    migrate(conn)
    repositories = {
        "settings_repo": SettingsRepository(conn),
        "jobs_repo": JobRepository(conn),
        "checks_repo": ChecksRepository(conn),
        "rentals_repo": MailRentalRepository(conn),
        "proxy_repo": ProxyRepository(conn),
    }
    manager = _FakeManager()
    originals = {name: getattr(server, name, None) for name in (*repositories, "reg_manager")}
    original_provider = getattr(server, "_provider_for_source", None)
    original_context = server._build_context
    for name, value in repositories.items():
        setattr(server, name, value)
    server.reg_manager = manager
    server._build_context = lambda: object()
    server._provider_for_source = lambda source, proxy_url=None: _FakeProvider(source)
    repositories["settings_repo"].set("proxy.enabled", "false")
    repositories["settings_repo"].set("sms.smsbower.api_key", "fixture-key")
    repositories["settings_repo"].set("accstack.api_key", "fixture-key")
    repositories["proxy_repo"].replace_all(
        [{"value": "bootstrap.example:7999", "selected": True}]
    )
    client = TestClient(server.app)

    try:
        runtime = client.get("/api/jobs/status")
        if runtime.status_code != 200 or runtime.json() != {"running": False}:
            failures.append(
                f"registration runtime status is wrong: {runtime.status_code} {runtime.text}"
            )
        manager.running = True
        runtime = client.get("/api/jobs/status")
        if runtime.status_code != 200 or runtime.json() != {"running": True}:
            failures.append(
                f"active registration runtime status is wrong: {runtime.status_code} {runtime.text}"
            )
        manager.running = False

        response = client.get("/api/mail-sources/status?source=gmail_smsbower")
        if response.status_code != 200:
            failures.append(f"mail source status HTTP {response.status_code}: {response.text}")
        else:
            body = response.json()
            if body.get("balance") != 500 or body.get("products", [{}])[0].get("id") != "dr:gmail.com":
                failures.append(f"mail source status body is wrong: {body!r}")
            if response.headers.get("cache-control") != "no-store":
                failures.append("mail source status is cacheable")
        if client.get("/api/mail-sources/status?source=outlook").status_code != 400:
            failures.append("mail source status accepted invalid source")

        response = client.put(
            "/api/proxies",
            json={
                "items": [
                    {"value": "one.example:8001", "selected": True},
                    {"value": "two.example:8002", "selected": False},
                ],
            },
        )
        if response.status_code != 200:
            failures.append(f"proxy PUT HTTP {response.status_code}: {response.text}")
        proxy_body = client.get("/api/proxies").json()
        if not proxy_body.get("enabled") or proxy_body.get("selected") != 1 or proxy_body.get("total") != 2:
            failures.append(f"proxy GET body is wrong: {proxy_body!r}")
        before = repositories["proxy_repo"].list_all()
        invalid = client.put(
            "/api/proxies",
            json={
                "items": [
                    {"value": "new.example:9001", "selected": True},
                    {"value": "not-a-proxy", "selected": True},
                ],
            },
        )
        if invalid.status_code != 400:
            failures.append("invalid proxy line did not return HTTP 400")
        if repositories["proxy_repo"].list_all() != before:
            failures.append("invalid proxy PUT partially changed the list")
        all_off = client.put(
            "/api/proxies",
            json={
                "items": [
                    {"value": "one.example:8001", "selected": False},
                    {"value": "two.example:8002", "selected": False},
                ],
            },
        )
        if all_off.status_code != 400:
            failures.append("all-off proxy PUT did not return HTTP 400")
        if repositories["proxy_repo"].list_all() != before:
            failures.append("all-off proxy PUT changed the list")
        empty_enabled = client.put(
            "/api/proxies",
            json={"items": []},
        )
        if empty_enabled.status_code != 400:
            failures.append("enabled empty proxy list did not return HTTP 400")
        if repositories["proxy_repo"].list_all() != before:
            failures.append("enabled empty proxy PUT changed the list")

        repositories["settings_repo"].set("proxy.enabled", "false")
        gmail = client.post(
            "/api/jobs/start",
            json={
                "source": "gmail_smsbower",
                "rental_count": 2,
                "profile_region": "ko",
                "reg_mode": "http",
                "fallback_enabled": False,
                "concurrency": 2,
            },
        )
        if gmail.status_code != 200 or gmail.json().get("rental_count") != 2:
            failures.append(f"Gmail start failed: {gmail.status_code} {gmail.text}")
        elif not manager.rental_calls or manager.rental_calls[-1].get("profile_region") != "ko":
            failures.append(f"Gmail start payload was not forwarded: {manager.rental_calls!r}")

        accstack = client.post(
            "/api/jobs/start",
            json={
                "source": "gmail_accstack",
                "rental_count": 1,
                "profile_region": "in",
                "reg_mode": "browser",
                "fallback_enabled": False,
                "concurrency": 1,
            },
        )
        if accstack.status_code != 200:
            failures.append(f"AccStack start failed: {accstack.status_code} {accstack.text}")
        elif manager.rental_calls[-1].get("product_id") != "5":
            failures.append("single AccStack Gmail product was not auto-selected")

        retry_job_id = "gmail-retry-api"
        repositories["rentals_repo"].create(
            {
                "id": "rental-retry-api",
                "provider": "smsbower",
                "external_id": "fixture-rental",
                "base_email": "base@example.com",
                "status": "error",
                "created_at": time.time(),
            }
        )
        repositories["jobs_repo"].create(
            {
                "id": retry_job_id,
                "email": "base+alias@example.com",
                "combo": "base+alias@example.com",
                "mail_mode": "gmail_smsbower",
                "reg_mode": "http",
                "status": "error",
                "rental_id": "rental-retry-api",
                "source_email": "base@example.com",
                "created_at": time.time(),
            }
        )
        retry = client.post(
            "/api/jobs/retry",
            json={
                "job_ids": [retry_job_id],
                "reg_mode": "http",
                "fallback_enabled": False,
            },
        )
        if retry.status_code != 200 or retry.json().get("job_ids") != [retry_job_id]:
            failures.append(f"Gmail retry failed: {retry.status_code} {retry.text}")
        elif not manager.rental_retry_calls:
            failures.append("Gmail retry used the Outlook manager path")

        invalid_payloads = [
            {"source": "gmail_smsbower", "rental_count": 0, "profile_region": "vi"},
            {"source": "gmail_smsbower", "rental_count": "2", "profile_region": "vi"},
            {"source": "gmail_smsbower", "rental_count": 1, "profile_region": "xx"},
            {"source": "unknown", "input": "x"},
        ]
        for payload in invalid_payloads:
            if client.post("/api/jobs/start", json=payload).status_code != 400:
                failures.append(f"invalid start payload was accepted: {payload!r}")

        check_id = "check-log-api"
        repositories["checks_repo"].create(
            {
                "id": check_id,
                "email": "fixture@example.com",
                "combo": "fixture@example.com|secret",
                "status": "live",
                "plan": "free",
                "created_at": time.time(),
            }
        )
        repositories["checks_repo"].append_log(check_id, "line-one")
        log_response = client.get(f"/api/checks/{check_id}/logs")
        if log_response.status_code != 200 or log_response.json().get("lines") != ["line-one"]:
            failures.append(f"check logs API is wrong: {log_response.status_code} {log_response.text}")
        if log_response.headers.get("cache-control") != "no-store":
            failures.append("check logs API is cacheable")
        if client.get("/api/checks/missing/logs").status_code != 404:
            failures.append("missing check logs did not return 404")
        combo_export = client.get(
            "/api/checks/export?status=live&plan=free&fmt=combo"
        )
        if (
            combo_export.status_code != 200
            or combo_export.text.strip() != "fixture@example.com|secret"
        ):
            failures.append(
                "categorized combo export is wrong: "
                f"{combo_export.status_code} {combo_export.text!r}"
            )
        if combo_export.headers.get("cache-control") != "no-store":
            failures.append("categorized combo export is cacheable")
    finally:
        server._build_context = original_context
        if original_provider is None:
            delattr(server, "_provider_for_source")
        else:
            server._provider_for_source = original_provider
        for name, value in originals.items():
            setattr(server, name, value)
        conn.close()

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] operations api" if failures else "[ok] operations api")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
