from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from playwright.async_api import Error as PlaywrightError

from gpt_reg.config import Settings, ensure_runtime_dirs, load_settings
from gpt_reg.core.constants import EXIT_CANCELLED, EXIT_CHALLENGE, EXIT_ERROR, EXIT_OK
from gpt_reg.core.context import RunContext
from gpt_reg.core.exceptions import (
    BrowserPhaseError,
    ChallengeBlockedError,
    GptRegError,
    HttpRegError,
    JobCancelledError,
)
from gpt_reg.db import connect, migrate
from gpt_reg.db.repositories import ProxyRepository, SettingsRepository
from gpt_reg.fingerprint import get_profile, profile_for_seed
from gpt_reg.mail.providers import build_provider
from gpt_reg.models import BrowserHandoff, SignupRequest, SignupResult
from gpt_reg.phases.http import HttpPhaseError, run_http_phase
from gpt_reg.phases.mfa import MfaError, enable_2fa
from gpt_reg.phases.registry import get_phase
from gpt_reg.proxy.pool import ProxyPool


# Kết nối DB + migrate dùng chung cho cả process. Chạy 200 luồng mà mỗi job tự
# `connect()` + `migrate()` sẽ mở 200 kết nối và chạy 200 lượt DDL tranh nhau
# khoá — vô ích vì schema chỉ cần dựng một lần.
_shared_lock = threading.Lock()
_shared_conn: Any = None
_shared_root: Path | None = None

_HTTP_ENGINE_FAILURE_STEPS = frozenset(
    {"bootstrap", "cf_block", "csrf", "invalid_state", "prime", "signin"}
)
_BROWSER_ENGINE_FAILURE_STEPS = frozenset(
    {"deadline", "email_entry", "turnstile", "unknown_screen"}
)


def _fallback_eligible_error(exc: Exception, *, reg_mode: str | None = None) -> bool:
    """Only transport/browser-engine failures may switch registration engine."""
    if reg_mode == "browser" and isinstance(exc, PlaywrightError):
        return True
    if isinstance(exc, ChallengeBlockedError):
        return True
    if isinstance(exc, HttpRegError):
        return exc.step in _HTTP_ENGINE_FAILURE_STEPS
    if isinstance(exc, BrowserPhaseError):
        return exc.step in _BROWSER_ENGINE_FAILURE_STEPS
    return False


def _shared_connection(settings: Settings):
    global _shared_conn, _shared_root
    db_path = settings.runtime_dir / "data.db"
    with _shared_lock:
        if _shared_conn is None or _shared_root != db_path:
            ensure_runtime_dirs(settings)
            conn = connect(db_path)
            migrate(conn)
            repo = SettingsRepository(conn)
            repo.apply_defaults(
                {
                    "proxy.enabled": "false",
                    "mail_mode.provider": "outlook",
                    "mail.smsbower.alias_limit": "1",
                    "mail.accstack.alias_limit": "1",
                    "browser.geoip": "true" if settings.browser_geoip else "false",
                    "reg.headless": "true" if settings.browser_headless else "false",
                    "ui.theme": "light",
                    "web.port": str(settings.web_port),
                }
            )
            _shared_conn = conn
            _shared_root = db_path
        return _shared_conn


def _build_context(
    settings: Settings | None = None,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_account_created: Callable[[str], None] | None = None,
) -> RunContext:
    settings = settings or load_settings()
    conn = _shared_connection(settings)
    repo = SettingsRepository(conn)
    proxy_repo = ProxyRepository(conn)
    records = proxy_repo.list_all()
    legacy_pool = repo.get("proxy.pool") or ""
    if not records and legacy_pool.strip():
        proxy_repo.replace_all(
            [
                {"value": line.strip(), "selected": True}
                for line in legacy_pool.splitlines()
                if line.strip()
            ]
        )
        records = proxy_repo.list_all()
    enabled = str(repo.get("proxy.enabled") or "false").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    pool = ProxyPool.from_records(records, enabled=enabled)
    return RunContext(
        settings=settings,
        proxy_pool=pool,
        settings_repo=repo,
        should_cancel=should_cancel,
        on_account_created=on_account_created,
    )


def _resolve_proxy_url(request: SignupRequest, ctx: RunContext) -> str | None:
    from gpt_reg.proxy.format import materialize_proxy, proxy_url_for_httpx

    if request.proxy:
        return proxy_url_for_httpx(materialize_proxy(request.proxy))
    return ctx.proxy_pool.acquire_url()


def save_session_file(
    *,
    settings: Settings,
    result: SignupResult,
    path: Path | None = None,
) -> Path:
    sessions_dir = settings.runtime_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    safe = result.email.replace("@", "_at_").replace(".", "_")
    out = path or (sessions_dir / f"{safe}.json")

    # GỘP với file cũ, không ghi đè. File này là nguồn duy nhất của `mfa_secret`
    # và mật khẩu tài khoản — hai thứ không tái tạo được. Một lần chạy lại
    # (retry) không bật 2FA sẽ có `mfa_secret=None`; ghi đè thẳng là xoá sổ
    # secret và account đó vĩnh viễn không đăng nhập lại được. Đã xảy ra thật
    # với BraunbergerKlare74@hotmail.com.
    old: dict[str, Any] = {}
    if out.exists():
        try:
            loaded = json.loads(out.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                old = loaded
        except (OSError, json.JSONDecodeError):
            old = {}

    fresh = {
        "email": result.email,
        "password": result.password,
        "access_token": result.access_token,
        "session_token": result.session_token,
        "cookies": result.cookies,
        "user_agent": result.user_agent,
        "fingerprint_profile": result.fingerprint_profile,
        "mfa_secret": result.mfa_secret,
        "mfa_activated": result.mfa_activated,
    }
    payload = dict(old)
    for key, value in fresh.items():
        # Giá trị mới rỗng thì giữ giá trị cũ. `mfa_activated=False` cũng là
        # "không biết" ở lần chạy không đụng tới 2FA, nên đừng hạ cờ đang bật.
        if value in (None, "", [], {}) or (key == "mfa_activated" and not value):
            if key in payload:
                continue
        payload[key] = value

    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)  # ghi nguyên tử: mất điện giữa chừng không để lại file cụt
    return out


def _registration_timing(
    reg_mode: str, phase_seconds: float, extraction_seconds: float
) -> dict[str, float | None]:
    """Gắn thời gian vào đúng engine; HTTP phase 1 và extract đều là HTTP."""
    if reg_mode == "http":
        return {
            "browser_seconds": None,
            "http_seconds": round(phase_seconds + extraction_seconds, 1),
        }
    return {
        "browser_seconds": round(phase_seconds, 1),
        "http_seconds": round(extraction_seconds, 1),
    }


def _network_request_for_handoff(
    request: SignupRequest,
    handoff: BrowserHandoff,
) -> SignupRequest:
    """Use the HTTP rotation winner, while preserving the job identity seed."""
    identity = (
        handoff.user_agent,
        handoff.impersonate,
        handoff.fingerprint_profile,
    )
    if not any(value is not None for value in identity):
        return request
    if not all(isinstance(value, str) and value for value in identity):
        raise HttpPhaseError("HTTP handoff has a partial fingerprint identity")
    profile = get_profile(handoff.fingerprint_profile)
    if handoff.user_agent != profile.user_agent or handoff.impersonate != profile.impersonate:
        raise HttpPhaseError("HTTP handoff fingerprint fields do not match its profile")
    return request.model_copy(
        update={
            "fingerprint_profile": profile.name,
            "user_agent": profile.user_agent,
            "impersonate": profile.impersonate,
        }
    )


def run_signup(
    request: SignupRequest,
    *,
    settings: Settings | None = None,
    log: Callable[[str], None] | None = None,
    with_2fa: bool = False,
    session_file: Path | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_account_created: Callable[[str], None] | None = None,
) -> SignupResult:
    logger = log or (lambda msg: None)
    settings = settings or load_settings()
    ctx = _build_context(
        settings, should_cancel=should_cancel, on_account_created=on_account_created
    )

    if not request.outlook_combo:
        return SignupResult(
            ok=False,
            email=request.email,
            error="outlook_combo required",
            exit_code=EXIT_ERROR,
        )

    base_profile = (
        get_profile(request.fingerprint_profile)
        if request.fingerprint_profile
        else profile_for_seed(request.fingerprint_seed)
    )
    req = request.model_copy(
        update={
            "fingerprint_profile": base_profile.name,
            "user_agent": base_profile.user_agent,
            "impersonate": base_profile.impersonate,
        }
    )
    proxy_url = _resolve_proxy_url(req, ctx)
    if proxy_url:
        req = req.model_copy(update={"proxy": proxy_url})

    mail = build_provider(
        req.mail_provider,
        combo_line=req.outlook_combo,
        state_dir=ctx.settings.outlook_state_dir,
        proxy_url=proxy_url,
    )

    phase = get_phase(req.reg_mode)

    def _phase_label() -> str:
        return "http" if req.reg_mode == "http" else "browser"

    async def _run() -> SignupResult:
        try:
            t0 = time.monotonic()
            handoff = await phase.run(ctx, req, mail, log=logger)
            t_phase = time.monotonic()
            logger(f"[timing] {_phase_label()} {t_phase - t0:.1f}s")
            network_request = _network_request_for_handoff(req, handoff)
            logger("[signup] phase 2: HTTP extract session + access_token")
            phase2 = await run_http_phase(request=network_request, handoff=handoff, log=logger)
            t_http = time.monotonic()
            logger(f"[timing] http session {t_http - t_phase:.1f}s")
            access = phase2.get("access_token")
            if not access:
                return SignupResult(
                    ok=False,
                    email=req.email,
                    password=req.password,
                    error="missing access_token after http phase",
                    handoff=handoff,
                    exit_code=EXIT_ERROR,
                )
            email = str(phase2.get("authenticated_email") or req.email).strip()
            # Phase có thể đã đăng nhập bằng mật khẩu KHÁC mật khẩu combo (acc cũ
            # dùng mật khẩu tài khoản đã lưu). Ghi đúng cái đã dùng, nếu không
            # session file và export sẽ chứa mật khẩu hộp thư — sai và vô dụng.
            account_password = getattr(handoff, "account_password", None) or req.password
            timing = _registration_timing(
                req.reg_mode, t_phase - t0, t_http - t_phase
            )
            result = SignupResult(
                ok=True,
                email=email,
                password=account_password,
                handoff=handoff,
                access_token=access,
                session_token=phase2.get("session_token"),
                cookies=list(phase2.get("cookies") or []),
                user_agent=network_request.user_agent,
                fingerprint_profile=network_request.fingerprint_profile,
                exit_code=EXIT_OK,
                **timing,
            )

            if with_2fa:
                logger("[signup] enabling 2FA (TOTP)")
                await asyncio.sleep(1.2)
                try:
                    mfa = await enable_2fa(
                        access_token=access,
                        cookies=list(handoff.cookies),
                        fingerprint_profile=network_request.fingerprint_profile,
                        proxy=network_request.proxy,
                        activate=True,
                        log=logger,
                    )
                    t_mfa = time.monotonic()
                    logger(f"[timing] mfa {t_mfa - t_http:.1f}s total {t_mfa - t0:.1f}s")
                    result.mfa_secret = mfa.get("secret")
                    result.mfa_activated = bool(mfa.get("activated"))
                    result.mfa_seconds = round(t_mfa - t_http, 1)
                    logger(f"[signup] 2FA activated={result.mfa_activated}")
                except MfaError as exc:
                    return SignupResult(
                        ok=False,
                        email=email,
                        password=req.password,
                        error=str(exc),
                        access_token=access,
                        cookies=result.cookies,
                        user_agent=network_request.user_agent,
                        fingerprint_profile=network_request.fingerprint_profile,
                        exit_code=EXIT_ERROR,
                    )

            out_path = save_session_file(settings=settings, result=result, path=session_file)
            result.session_path = str(out_path)
            logger(f"[signup] session saved {out_path}")
            return result
        except JobCancelledError as exc:
            logger(f"[signup] {exc}")
            return SignupResult(
                ok=False,
                email=req.email,
                error="cancelled",
                exit_code=EXIT_CANCELLED,
            )
        except ChallengeBlockedError as exc:
            return SignupResult(
                ok=False,
                email=req.email,
                error=str(exc),
                fallback_eligible=True,
                exit_code=EXIT_CHALLENGE,
            )
        except (BrowserPhaseError, HttpRegError) as exc:
            return SignupResult(
                ok=False,
                email=req.email,
                error=str(exc),
                fallback_eligible=_fallback_eligible_error(exc, reg_mode=req.reg_mode),
                exit_code=EXIT_ERROR,
            )
        except (HttpPhaseError, GptRegError) as exc:
            return SignupResult(
                ok=False,
                email=req.email,
                error=str(exc),
                exit_code=EXIT_ERROR,
            )
        except Exception as exc:
            return SignupResult(
                ok=False,
                email=req.email,
                error=f"{type(exc).__name__}: {exc}",
                fallback_eligible=_fallback_eligible_error(exc, reg_mode=req.reg_mode),
                exit_code=EXIT_ERROR,
            )

    return asyncio.run(_run())
