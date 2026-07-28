from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from gpt_reg.config import load_settings
from gpt_reg.checker.combo import CheckComboError
from gpt_reg.db import connect, migrate
from gpt_reg.db.repositories import (
    ChecksRepository,
    JobRepository,
    MailRentalRepository,
    ProxyRepository,
    SettingsRepository,
)
from gpt_reg.mail.accstack import AccStackMailRentalProvider
from gpt_reg.mail.modes import serialize_for_api
from gpt_reg.mail.rental import MailRentalError
from gpt_reg.mail.smsbower_rental import SmsBowerMailRentalProvider
from gpt_reg.proxy.format import materialize_proxy
from gpt_reg.proxy.pool import ProxyPool
from gpt_reg.signup import _build_context  # kept for compatibility; job API no longer calls it
from gpt_reg.web import export
from gpt_reg.web.jobs.check_manager import (
    CHECK_CONCURRENCY_CHOICES,
    MAX_CONCURRENCY_CHECK,
    clamp_check_concurrency,
    get_check_manager,
)
from gpt_reg.web.jobs.reg_manager import (
    CONCURRENCY_CHOICES,
    MAX_CONCURRENCY_BROWSER,
    MAX_CONCURRENCY_HTTP,
    InvalidComboError,
    clamp_concurrency,
    sanitize_job_log_line,
)
from gpt_reg.web.jobs.registry import get_job_manager

settings = load_settings()
conn = connect(settings.runtime_dir / "data.db")
migrate(conn)
settings_repo = SettingsRepository(conn)
jobs_repo = JobRepository(conn)
checks_repo = ChecksRepository(conn)
rentals_repo = MailRentalRepository(conn)
proxy_repo = ProxyRepository(conn)
# Worker chết theo process trước; job của chúng không bao giờ chạy tiếp.
_orphans = jobs_repo.reap_orphans()
_orphan_checks = checks_repo.reap_orphans()
# Job bị kill giữa chừng để lại profile Camoufox ~37 MB mỗi cái.
from gpt_reg.phases.browser import reap_stale_profiles as _reap_profiles  # noqa: E402

_stale_profiles = _reap_profiles(settings.profiles_dir)
reg_manager = get_job_manager("reg")
check_manager = get_check_manager()


app = FastAPI(title="Gpt_reg")
STATIC = settings.root_dir / "gpt_reg" / "web" / "static"


def _no_store(payload: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        payload,
        status_code=status_code,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _runtime_proxy_pool() -> ProxyPool:
    return ProxyPool.from_records(
        proxy_repo.list_all(),
        enabled=True,
    )


def _provider_for_source(source: str, proxy_url: str | None = None):
    if source == "gmail_smsbower":
        api_key = settings_repo.get("sms.smsbower.api_key")
        if not api_key:
            raise ValueError("SMSBower API key is not configured")
        return SmsBowerMailRentalProvider(api_key, proxy_url=proxy_url)
    if source == "gmail_accstack":
        api_key = settings_repo.get("accstack.api_key")
        if not api_key:
            raise ValueError("AccStack API key is not configured")
        return AccStackMailRentalProvider(api_key, proxy_url=proxy_url)
    raise ValueError(f"unsupported mail source: {source!r}")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (STATIC / "app" / "index.html").read_text(encoding="utf-8")
    response = HTMLResponse(html)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@app.get("/api/mail-modes")
def mail_modes() -> list[dict[str, Any]]:
    return serialize_for_api()


@app.get("/api/limits")
def limits() -> dict[str, Any]:
    """Mức luồng UI được phép chọn, kèm trần theo chế độ."""
    return {
        "concurrency_choices": list(CONCURRENCY_CHOICES),
        "max_browser": MAX_CONCURRENCY_BROWSER,
        "max_http": MAX_CONCURRENCY_HTTP,
        "check_concurrency_choices": list(CHECK_CONCURRENCY_CHOICES),
        "max_check": MAX_CONCURRENCY_CHECK,
    }


@app.get("/api/settings")
def get_settings() -> dict[str, str | None]:
    return settings_repo.all_known()


@app.get("/api/settings/integration-keys")
def get_integration_keys() -> JSONResponse:
    return _no_store(
        {
            "sms.smsbower.api_key": settings_repo.get("sms.smsbower.api_key"),
            "accstack.api_key": settings_repo.get("accstack.api_key"),
        }
    )


@app.post("/api/settings")
async def post_settings(payload: dict[str, str]) -> dict[str, str]:
    from gpt_reg.db.repositories import MASKED_VALUE

    for key, val in payload.items():
        # UI hiển thị secret dạng che; gửi lại nguyên xi nghĩa là "không đổi".
        if val == MASKED_VALUE:
            continue
        settings_repo.set(key, val)
    return {"ok": "true"}


MAIL_RENTAL_SOURCES = ("gmail_smsbower", "gmail_accstack")


@app.get("/api/mail-sources/status")
def mail_source_status(source: str) -> JSONResponse:
    if source not in MAIL_RENTAL_SOURCES:
        raise HTTPException(status_code=400, detail="unsupported mail source")
    key_name = (
        "sms.smsbower.api_key" if source == "gmail_smsbower" else "accstack.api_key"
    )
    if not settings_repo.get(key_name):
        return _no_store(
            {
                "configured": False,
                "balance": 0,
                "currency": "USD",
                "price": 0,
                "stock": 0,
                "affordable": 0,
                "products": [],
                "reason": f"{source} API key is not configured",
            }
        )
    try:
        proxy_url = _runtime_proxy_pool().acquire_url()
        status = _provider_for_source(source, proxy_url=proxy_url).status()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MailRentalError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _no_store(asdict(status))


@app.get("/api/proxies")
def get_proxies() -> JSONResponse:
    items = proxy_repo.list_all()
    return _no_store(
        {
            "enabled": True,
            "items": items,
            "selected": sum(1 for item in items if item["selected"]),
            "total": len(items),
        }
    )


@app.put("/api/proxies")
async def put_proxies(payload: dict[str, Any]) -> JSONResponse:
    items = payload.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items must be a list")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=400,
                detail={"line": index, "message": "proxy row must be an object"},
            )
        value = item.get("value")
        selected = item.get("selected")
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(
                status_code=400,
                detail={"line": index, "message": "proxy value is required"},
            )
        if type(selected) is not bool:
            raise HTTPException(
                status_code=400,
                detail={"line": index, "message": "selected must be a boolean"},
            )
        try:
            materialize_proxy(value.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"line": index, "message": str(exc)},
            ) from exc
        normalized.append({"value": value.strip(), "selected": selected})
    try:
        ProxyPool.from_records(normalized, enabled=True)
        proxy_repo.replace_all(normalized)
        settings_repo.set("proxy.enabled", "true")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_proxies()


@app.get("/api/sms/status")
def sms_status() -> JSONResponse:
    """Số dư + tồn kho số cho nguồn Gmail (SMSBower).

    Trả `configured=False` thay vì lỗi khi chưa nhập API key — UI hiển thị nhắc
    nhập thay vì báo đỏ.
    """
    api_key = settings_repo.get("sms.smsbower.api_key")
    if not api_key:
        return _no_store(
            {"configured": False, "reason": "SMSBower API key is not configured"}
        )

    from gpt_reg.sms import SmsBowerClient, SmsBowerError

    try:
        proxy_url = _runtime_proxy_pool().acquire_url()
        client = SmsBowerClient(api_key, proxy_url=proxy_url)
        balance = client.get_balance()
        countries = client.get_countries()
        stocks = client.get_availability(countries=countries, limit=25)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SmsBowerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    total = sum(s.count for s in stocks)
    cheapest = min((s for s in stocks), key=lambda s: s.cost, default=None)
    return {
        "configured": True,
        "ok": True,
        "balance": round(balance, 3),
        "total_available": total,
        "affordable": int(balance / cheapest.cost) if cheapest and cheapest.cost > 0 else 0,
        "countries": [
            {
                "id": s.country_id,
                "name": s.country_name,
                "cost": s.cost,
                "count": s.count,
                "affordable": int(balance / s.cost) if s.cost > 0 else 0,
            }
            for s in stocks
        ],
    }


def _clean_reg_mode(value: Any) -> str:
    mode = str(value or "browser").strip()
    from gpt_reg.phases.registry import available_modes

    if mode not in available_modes():
        raise HTTPException(status_code=400, detail="unsupported registration mode")
    return mode


def _json_bool(payload: dict[str, Any], key: str, *, default: bool = False) -> bool:
    """Đọc JSON boolean nghiêm ngặt để chuỗi `"false"` không thành True."""
    if key not in payload:
        return default
    value = payload[key]
    if type(value) is not bool:
        raise HTTPException(status_code=400, detail=f"{key} must be a boolean")
    return value


REG_SOURCES = ("outlook", *MAIL_RENTAL_SOURCES)
PROFILE_REGIONS = ("vi", "ko", "in")


def _clean_source(value: Any) -> str:
    source = str(value or "outlook").strip().lower()
    if source not in REG_SOURCES:
        raise HTTPException(status_code=400, detail="unsupported registration source")
    return source


def _clean_profile_region(value: Any) -> str:
    region = str(value or "vi").strip().lower()
    if region not in PROFILE_REGIONS:
        raise HTTPException(status_code=400, detail="unsupported profile region")
    return region


def _alias_limit(source: str) -> int:
    key = (
        "mail.smsbower.alias_limit"
        if source == "gmail_smsbower"
        else "mail.accstack.alias_limit"
    )
    raw = settings_repo.get(key) or "1"
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"invalid setting: {key}") from exc
    if value < 1 or value > 50:
        raise HTTPException(status_code=500, detail=f"invalid setting: {key}")
    return value


def _job_for_api(row: dict[str, Any]) -> dict[str, Any]:
    """Chỉ trả field UI cần; combo/password/session_path không rời server."""
    public_fields = (
        "id",
        "email",
        "mail_mode",
        "reg_mode",
        "status",
        "error",
        "mfa_activated",
        "browser_seconds",
        "http_seconds",
        "mfa_seconds",
        "created_at",
        "started_at",
        "finished_at",
        "registered_at",
        "profile_region",
        "alias_index",
    )
    public = {key: row.get(key) for key in public_fields if key in row}
    if public.get("error") is not None:
        public["error"] = sanitize_job_log_line(str(public["error"]))
    return public


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    return [_job_for_api(r) for r in jobs_repo.list_recent()]


@app.get("/api/jobs/status")
def jobs_status() -> JSONResponse:
    return _no_store({"running": reg_manager.running})


@app.get("/api/jobs/export")
def export_jobs(
    fmt: str = "combo",
    status: str = "success",
) -> PlainTextResponse:
    """Xuất kết quả. `fmt`: combo | combo_mail | json. `status`: success | all.

    combo_mail ghép cả combo mail gốc — chỉ chạy khi người dùng chủ động bấm xuất.
    """
    if fmt not in export.FORMATS:
        raise HTTPException(status_code=400, detail=f"fmt phải là một trong {export.FORMATS}")
    if status == "all":
        rows = jobs_repo.list_recent(limit=None)  # xuất là phải đủ, không cắt
    else:
        rows = jobs_repo.list_by_status(("success",))
    records = export.build_records(rows, read_secret=export.read_secret_from_session_file)
    text, media = export.render(records, fmt)
    return PlainTextResponse(text, media_type=media)


@app.post("/api/jobs/start")
async def start_jobs(payload: dict[str, Any]) -> dict[str, Any]:
    headless = _json_bool(payload, "headless")
    with_2fa = _json_bool(payload, "with_2fa")
    reg_mode = _clean_reg_mode(payload.get("reg_mode"))
    fallback_enabled = _json_bool(payload, "fallback_enabled")
    source = _clean_source(payload.get("source"))
    profile_region = _clean_profile_region(payload.get("profile_region"))
    settings_repo.set("reg.source", source)
    concurrency = clamp_concurrency(payload.get("concurrency"), reg_mode, fallback_enabled)
    try:
        pool = _runtime_proxy_pool()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if source == "outlook":
        text = str(payload.get("input") or "")
        combos = [line.strip() for line in text.splitlines() if line.strip()]
        if not combos:
            raise HTTPException(status_code=400, detail="No Hotmail/Outlook combos provided")
        try:
            ids = reg_manager.start_batch(
                combos=combos,
                headless=headless,
                jobs_repo=jobs_repo,
                with_2fa=with_2fa,
                reg_mode=reg_mode,
                fallback_enabled=fallback_enabled,
                concurrency=concurrency,
                profile_region=profile_region,
                proxy_pool=pool,
            )
        except InvalidComboError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid combo: {exc}") from exc
        return {"job_ids": ids, "concurrency": min(concurrency, len(ids)) if ids else 0}

    rental_count = payload.get("rental_count")
    if type(rental_count) is not int or not 1 <= rental_count <= 200:
        raise HTTPException(
            status_code=400,
            detail="rental_count must be an integer from 1 to 200",
        )

    try:
        status = _provider_for_source(source, proxy_url=pool.acquire_url()).status()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MailRentalError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    products = {product.id: product for product in status.products}
    requested_product = payload.get("product_id")
    if requested_product is not None and not isinstance(requested_product, (str, int)):
        raise HTTPException(status_code=400, detail="product_id must be a string or integer")
    product_id = str(requested_product).strip() if requested_product is not None else ""
    if not product_id:
        if len(products) != 1:
            raise HTTPException(status_code=400, detail="product_id is required")
        product_id = next(iter(products))
    product = products.get(product_id)
    if product is None:
        raise HTTPException(status_code=400, detail="mail product is unavailable")
    affordable = status.balance // product.price if product.price else 0
    if rental_count > min(product.stock, affordable):
        raise HTTPException(status_code=400, detail="rental_count exceeds stock or balance")

    def provider_factory():
        return _provider_for_source(source, proxy_url=pool.acquire_url())

    try:
        rental_ids = reg_manager.start_rental_batch(
            rental_count=rental_count,
            provider_factory=provider_factory,
            jobs_repo=jobs_repo,
            rentals_repo=rentals_repo,
            source=source,
            product_id=product_id,
            alias_limit=_alias_limit(source),
            profile_region=profile_region,
            headless=headless,
            with_2fa=with_2fa,
            reg_mode=reg_mode,
            fallback_enabled=fallback_enabled,
            concurrency=concurrency,
            balance_before=status.balance,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "rental_ids": rental_ids,
        "rental_count": len(rental_ids),
        "concurrency": min(concurrency, len(rental_ids)) if rental_ids else 0,
    }


@app.get("/api/jobs/{job_id}/logs")
def job_logs(job_id: str) -> JSONResponse:
    if not jobs_repo.get(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    return _no_store({
        "job_id": job_id,
        "lines": [sanitize_job_log_line(line) for line in jobs_repo.logs(job_id, limit=500)],
    })


@app.post("/api/jobs/stop")
async def stop_jobs(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = payload.get("job_id")
    if job_id:
        reg_manager.stop_job(str(job_id))
        return {"stopped": [job_id]}
    reg_manager.stop_all()
    return {"stopped": "all"}


@app.post("/api/jobs/retry")
async def retry_jobs(payload: dict[str, Any]) -> dict[str, Any]:
    """Chạy lại job lỗi/huỷ — giữ nguyên job id nên lịch sử không bị phân mảnh."""
    requested = payload.get("job_ids")
    if requested:
        rows = [jobs_repo.get(str(i)) for i in requested]
        targets = [r for r in rows if r and r["status"] in ("error", "cancelled")]
    else:
        targets = jobs_repo.list_by_status(("error", "cancelled"))
    if not targets:
        return {"job_ids": []}
    retry_mode = _clean_reg_mode(payload.get("reg_mode"))
    fallback_enabled = _json_bool(payload, "fallback_enabled")
    headless = _json_bool(payload, "headless")
    with_2fa = _json_bool(payload, "with_2fa")
    concurrency = clamp_concurrency(
        payload.get("concurrency"), retry_mode, fallback_enabled
    )
    gmail_targets = [
        row
        for row in targets
        if row.get("mail_mode") in MAIL_RENTAL_SOURCES
    ]
    outlook_targets = [
        row
        for row in targets
        if row.get("mail_mode") not in MAIL_RENTAL_SOURCES
    ]
    if gmail_targets and outlook_targets:
        raise HTTPException(
            status_code=400,
            detail="retry Hotmail/Outlook and Gmail in separate batches",
        )
    try:
        pool = _runtime_proxy_pool()
        if gmail_targets:
            def provider_factory(source: str):
                return _provider_for_source(source, proxy_url=pool.acquire_url())

            ids = reg_manager.start_rental_retry_batch(
                job_ids=[str(row["id"]) for row in gmail_targets],
                provider_factory=provider_factory,
                jobs_repo=jobs_repo,
                rentals_repo=rentals_repo,
                headless=headless,
                with_2fa=with_2fa,
                reg_mode=retry_mode,
                fallback_enabled=fallback_enabled,
                concurrency=concurrency,
            )
        else:
            ids = reg_manager.start_batch(
                combos=[],
                headless=headless,
                jobs_repo=jobs_repo,
                with_2fa=with_2fa,
                reg_mode=retry_mode,
                fallback_enabled=fallback_enabled,
                concurrency=concurrency,
                job_ids=[str(row["id"]) for row in outlook_targets],
                proxy_pool=pool,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job_ids": ids}


@app.post("/api/jobs/clear")
async def clear_jobs(payload: dict[str, Any]) -> dict[str, Any]:
    """`scope=done` xoá job xong xuôi, giữ lại job lỗi để còn retry.
    `scope=all` xoá mọi job đã kết thúc.

    Job `running`/`queued` không bao giờ bị xoá — worker vẫn đang ghi vào chúng.
    """
    scope = str(payload.get("scope") or "done")
    if scope == "all":
        statuses = ("success", "error", "cancelled")
    else:
        statuses = ("success", "cancelled")
    requested = payload.get("job_ids")
    if requested is not None:
        if not isinstance(requested, (list, tuple)):
            raise HTTPException(status_code=400, detail="job_ids must be a list")
        job_ids = tuple(str(job_id) for job_id in requested)
        removed = jobs_repo.delete_ids(job_ids, allowed_statuses=("success", "error", "cancelled"))
    else:
        removed = jobs_repo.delete_by_status(statuses)
    return {"removed": removed, "scope": scope}


# ─── Check acc ────────────────────────────────────────────────────────────


def _check_for_api(row: dict[str, Any]) -> dict[str, Any]:
    public = {
        "id": row["id"],
        "email": row["email"],
        "status": row["status"],
        "plan": row.get("plan"),
        "plan_detail": row.get("plan_detail"),
        "has_subscription": bool(row.get("has_subscription")),
        "expires_at": row.get("expires_at"),
        "mfa_enabled": bool(row.get("mfa_enabled")),
        "deactivated": bool(row.get("deactivated")),
        "error": row.get("error"),
        "seconds": row.get("seconds"),
    }
    if public.get("error") is not None:
        public["error"] = sanitize_job_log_line(str(public["error"]))
    return public


@app.get("/api/checks")
def list_checks() -> list[dict[str, Any]]:
    return [_check_for_api(r) for r in checks_repo.list_recent()]


@app.get("/api/checks/{check_id}/logs")
def check_logs(check_id: str) -> JSONResponse:
    if not checks_repo.get(check_id):
        raise HTTPException(status_code=404, detail="check not found")
    return _no_store(
        {
            "check_id": check_id,
            "lines": [
                sanitize_job_log_line(line)
                for line in checks_repo.logs(check_id, limit=500)
            ],
        }
    )


@app.post("/api/checks/start")
async def start_checks(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("input") or "")
    combos = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not combos:
        raise HTTPException(status_code=400, detail="Chưa có combo nào.")
    concurrency = clamp_check_concurrency(payload.get("concurrency"))
    try:
        records = proxy_repo.list_all()
        ProxyPool.from_records(records, enabled=True)
        ids = check_manager.start_batch(
            combos=combos,
            checks_repo=checks_repo,
            proxy_pool_text="",
            proxy_records=records,
            proxy_enabled=True,
            concurrency=concurrency,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CheckComboError as exc:
        raise HTTPException(status_code=400, detail=f"Combo sai — {exc}") from exc
    return {"check_ids": ids, "concurrency": min(concurrency, len(ids)) if ids else 0}


@app.post("/api/checks/stop")
async def stop_checks(payload: dict[str, Any]) -> dict[str, Any]:
    check_id = payload.get("check_id")
    if check_id:
        check_manager.stop_check(str(check_id))
        return {"stopped": [check_id]}
    check_manager.stop_all()
    return {"stopped": "all"}


@app.post("/api/checks/retry")
async def retry_checks(payload: dict[str, Any]) -> dict[str, Any]:
    """Chạy lại check lỗi/huỷ (không tính `die` — đó là kết quả chắc chắn)."""
    requested = payload.get("check_ids")
    if requested:
        rows = [checks_repo.get(str(i)) for i in requested]
        targets = [r for r in rows if r and r["status"] in ("error", "cancelled")]
    else:
        targets = checks_repo.list_by_status(("error", "cancelled"))
    if not targets:
        return {"check_ids": []}
    records = proxy_repo.list_all()
    try:
        ProxyPool.from_records(records, enabled=True)
        ids = check_manager.start_batch(
            combos=[],
            checks_repo=checks_repo,
            proxy_pool_text="",
            proxy_records=records,
            proxy_enabled=True,
            concurrency=clamp_check_concurrency(payload.get("concurrency")),
            check_ids=[str(r["id"]) for r in targets],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"check_ids": ids}


@app.post("/api/checks/clear")
async def clear_checks(payload: dict[str, Any]) -> dict[str, Any]:
    scope = str(payload.get("scope") or "done")
    if scope == "all":
        statuses = ("live", "die", "onboarding", "error", "cancelled")
    else:
        statuses = ("live", "die", "onboarding")
    removed = checks_repo.delete_by_status(statuses)
    return {"removed": removed, "scope": scope}


@app.get("/api/checks/export")
def export_checks(
    status: str = "live",
    plan: str | None = None,
    fmt: str = "summary",
) -> PlainTextResponse:
    """Xuất `email|plan|2fa` cho các check đọc được plan.

    `status=live` chỉ account đăng nhập được; `status=all` tất cả (kèm cả lỗi để
    còn soi). Mỗi dòng: `email|plan|has_sub|mfa`.
    """
    if status not in {"live", "all"}:
        raise HTTPException(status_code=400, detail="invalid check export status")
    if plan not in {None, "free", "plus"}:
        raise HTTPException(status_code=400, detail="invalid check export plan")
    if fmt not in {"summary", "combo"}:
        raise HTTPException(status_code=400, detail="invalid check export format")

    if status == "all":
        rows = checks_repo.list_recent(limit=None)
    else:
        rows = checks_repo.list_by_status(("live",))
    if plan:
        rows = [
            row
            for row in rows
            if plan
            in f"{row.get('plan') or ''} {row.get('plan_detail') or ''}".lower()
        ]

    if fmt == "combo":
        lines = [str(row.get("combo") or "").strip() for row in rows]
        lines = [line for line in lines if line]
        return PlainTextResponse(
            "\n".join(lines) + ("\n" if lines else ""),
            media_type="text/plain",
            headers={"Cache-Control": "no-store"},
        )

    lines = []
    for r in rows:
        plan = r.get("plan") or "?"
        sub = "sub" if r.get("has_subscription") else "nosub"
        mfa = "2fa" if r.get("mfa_enabled") else "no2fa"
        st = r.get("status")
        tail = plan if st == "live" else f"{st}:{(r.get('error') or '')[:40]}"
        lines.append(f"{r['email']}|{tail}|{sub}|{mfa}")
    return PlainTextResponse(
        "\n".join(lines) + ("\n" if lines else ""),
        media_type="text/plain",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/sse")
async def sse(request: Request) -> StreamingResponse:
    # Manager phát event từ worker thread, còn queue thuộc event loop.
    # `asyncio.Queue` không thread-safe: gọi put_nowait thẳng từ thread khác vừa
    # đua với nội bộ queue, vừa không đánh thức coroutine đang chờ — event nằm im
    # tới nhịp timeout 15s kế tiếp. `call_soon_threadsafe` là cầu nối đúng.
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)

    def on_event(ev: dict[str, Any]) -> None:
        def _put() -> None:
            try:
                queue.put_nowait(ev)
            except asyncio.QueueFull:
                pass  # client tiêu thụ không kịp — bỏ event cũ hơn là chặn worker

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass  # loop đã đóng

    # Một stream duy nhất cho cả hai kênh: event check mang sẵn `scope="check"`
    # nên client phân biệt được. Đỡ phải mở hai kết nối SSE.
    reg_manager.subscribe(on_event)
    check_manager.subscribe(on_event)

    async def gen():
        try:
            yield f"data: {json.dumps({'type': 'hello', 'channel': 'reg'})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # comment SSE — client không phải parse
        finally:
            reg_manager.unsubscribe(on_event)
            check_manager.unsubscribe(on_event)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # tắt buffer nếu chạy sau nginx
        },
    )


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
