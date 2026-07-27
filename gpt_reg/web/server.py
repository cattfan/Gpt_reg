from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from gpt_reg.config import load_settings
from gpt_reg.checker.combo import CheckComboError
from gpt_reg.db import connect, migrate
from gpt_reg.db.repositories import ChecksRepository, JobRepository, SettingsRepository
from gpt_reg.mail.modes import serialize_for_api
from gpt_reg.proxy.pool import ProxyPool
from gpt_reg.signup import _build_context
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


@app.post("/api/settings")
async def post_settings(payload: dict[str, str]) -> dict[str, str]:
    from gpt_reg.db.repositories import MASKED_VALUE

    for key, val in payload.items():
        # UI hiển thị secret dạng che; gửi lại nguyên xi nghĩa là "không đổi".
        if val == MASKED_VALUE:
            continue
        settings_repo.set(key, val)
    return {"ok": "true"}


@app.get("/api/sms/status")
def sms_status() -> dict[str, Any]:
    """Số dư + tồn kho số cho nguồn Gmail (SMSBower).

    Trả `configured=False` thay vì lỗi khi chưa nhập API key — UI hiển thị nhắc
    nhập thay vì báo đỏ.
    """
    api_key = settings_repo.get("sms.smsbower.api_key")
    if not api_key:
        return {"configured": False, "reason": "chưa có API key SMSBower"}

    from gpt_reg.sms import SmsBowerClient, SmsBowerError

    proxy_url = None
    try:
        pool = ProxyPool.from_multiline(settings_repo.get("proxy.pool") or "")
        proxy_url = pool.acquire_url()
    except Exception:
        pass

    client = SmsBowerClient(api_key, proxy_url=proxy_url)
    try:
        balance = client.get_balance()
        countries = client.get_countries()
        stocks = client.get_availability(countries=countries, limit=25)
    except SmsBowerError as exc:
        return {"configured": True, "ok": False, "error": str(exc)}

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

    return mode if mode in available_modes() else "browser"


def _json_bool(payload: dict[str, Any], key: str, *, default: bool = False) -> bool:
    """Đọc JSON boolean nghiêm ngặt để chuỗi `"false"` không thành True."""
    if key not in payload:
        return default
    value = payload[key]
    if type(value) is not bool:
        raise HTTPException(status_code=400, detail=f"{key} must be a boolean")
    return value


# Nguồn tài khoản mail dùng để đăng ký. "gmail" mới có phần hạ tầng SMS
# (số dư + tồn kho); luồng tạo Gmail bằng số thuê CHƯA làm.
REG_SOURCES = ("outlook", "gmail")


def _clean_source(value: Any) -> str:
    source = str(value or "outlook").strip().lower()
    return source if source in REG_SOURCES else "outlook"


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
    )
    public = {key: row.get(key) for key in public_fields if key in row}
    if public.get("error") is not None:
        public["error"] = sanitize_job_log_line(str(public["error"]))
    return public


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    return [_job_for_api(r) for r in jobs_repo.list_recent()]


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
    text = str(payload.get("input") or "")
    combos = [ln.strip() for ln in text.splitlines() if ln.strip()]
    headless = bool(payload.get("headless"))
    with_2fa = bool(payload.get("with_2fa"))
    reg_mode = _clean_reg_mode(payload.get("reg_mode"))
    fallback_enabled = _json_bool(payload, "fallback_enabled")
    source = _clean_source(payload.get("source"))
    if source == "gmail":
        # Chưa có luồng tạo Gmail bằng số thuê — từ chối rõ ràng thay vì âm thầm
        # chạy như Hotmail rồi fail ở chỗ khó hiểu.
        raise HTTPException(
            status_code=400,
            detail="Nguồn Gmail chưa hỗ trợ đăng ký tự động — mới có phần số dư/tồn kho SMS.",
        )
    settings_repo.set("reg.source", source)
    if not combos:
        raise HTTPException(status_code=400, detail="Chưa có combo nào.")
    concurrency = clamp_concurrency(payload.get("concurrency"), reg_mode, fallback_enabled)
    ctx = _build_context()
    try:
        ids = reg_manager.start_batch(
            combos=combos,
            headless=headless,
            jobs_repo=jobs_repo,
            ctx=ctx,
            with_2fa=with_2fa,
            reg_mode=reg_mode,
            fallback_enabled=fallback_enabled,
            concurrency=concurrency,
        )
    except InvalidComboError as exc:
        # 400 kèm số dòng để người dùng sửa được, thay vì 500 chung chung.
        raise HTTPException(status_code=400, detail=f"Combo sai — {exc}") from exc
    return {"job_ids": ids, "concurrency": min(concurrency, len(ids)) if ids else 0}


@app.get("/api/jobs/{job_id}/logs")
def job_logs(job_id: str) -> dict[str, Any]:
    if not jobs_repo.get(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": job_id,
        "lines": [sanitize_job_log_line(line) for line in jobs_repo.logs(job_id, limit=500)],
    }


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
    ids = reg_manager.start_batch(
        combos=[],
        headless=bool(payload.get("headless")),
        jobs_repo=jobs_repo,
        ctx=_build_context(),
        with_2fa=bool(payload.get("with_2fa")),
        reg_mode=retry_mode,
        fallback_enabled=fallback_enabled,
        concurrency=clamp_concurrency(
            payload.get("concurrency"), retry_mode, fallback_enabled
        ),
        job_ids=[str(r["id"]) for r in targets],
    )
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
    return {
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


@app.get("/api/checks")
def list_checks() -> list[dict[str, Any]]:
    return [_check_for_api(r) for r in checks_repo.list_recent()]


@app.post("/api/checks/start")
async def start_checks(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("input") or "")
    combos = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not combos:
        raise HTTPException(status_code=400, detail="Chưa có combo nào.")
    concurrency = clamp_check_concurrency(payload.get("concurrency"))
    try:
        ids = check_manager.start_batch(
            combos=combos,
            checks_repo=checks_repo,
            proxy_pool_text=settings_repo.get("proxy.pool") or "",
            rotation_mode=settings_repo.get("proxy.rotation_mode") or "round_robin",
            concurrency=concurrency,
        )
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
    ids = check_manager.start_batch(
        combos=[],
        checks_repo=checks_repo,
        proxy_pool_text=settings_repo.get("proxy.pool") or "",
        rotation_mode=settings_repo.get("proxy.rotation_mode") or "round_robin",
        concurrency=clamp_check_concurrency(payload.get("concurrency")),
        check_ids=[str(r["id"]) for r in targets],
    )
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
def export_checks(status: str = "live") -> PlainTextResponse:
    """Xuất `email|plan|2fa` cho các check đọc được plan.

    `status=live` chỉ account đăng nhập được; `status=all` tất cả (kèm cả lỗi để
    còn soi). Mỗi dòng: `email|plan|has_sub|mfa`.
    """
    if status == "all":
        rows = checks_repo.list_recent(limit=None)
    else:
        rows = checks_repo.list_by_status(("live",))
    lines = []
    for r in rows:
        plan = r.get("plan") or "?"
        sub = "sub" if r.get("has_subscription") else "nosub"
        mfa = "2fa" if r.get("mfa_enabled") else "no2fa"
        st = r.get("status")
        tail = plan if st == "live" else f"{st}:{(r.get('error') or '')[:40]}"
        lines.append(f"{r['email']}|{tail}|{sub}|{mfa}")
    return PlainTextResponse("\n".join(lines) + ("\n" if lines else ""), media_type="text/plain")


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
