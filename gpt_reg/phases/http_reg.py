"""Đăng ký thuần HTTP (reg_mode="http") — không cần browser.

Port từ `privateGSH/request_phase.py` (bản này lại adapt từ
github.com/Regert888/gpt-outlook-register). Chạy state machine signup của OpenAI
qua curl_cffi:

  prime CF cookies → csrf → signin/openai → oauth init → GET create-account/password
  → sentinel + POST user/register → email-otp/send → poll OTP (Graph) → validate
  → sentinel + create_account → follow redirects → consume callback → /api/auth/session

Sentinel token dùng QuickJS (Node, real sdk.js). Python PoW chỉ chạy khi người
vận hành tắt QuickJS rõ ràng qua cấu hình môi trường.

Khác GSH: OTP poll qua `MailProvider.wait_for_otp` (Graph, không IMAP) tái dùng
`phases/browser/otp.poll_code` (đã có slicing + cancel + retry SSL); huỷ hợp tác
qua `ctx.should_cancel`; trả về `BrowserHandoff` để dùng chung downstream.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlencode, urlparse

from curl_cffi import requests as curl_requests

from gpt_reg.core.context import RunContext
from gpt_reg.core.contracts import MailProvider
from gpt_reg.core.exceptions import HttpRegError, JobCancelledError
from gpt_reg.fingerprint import (
    Profile,
    candidate_profiles,
    device_id_for_seed,
    get_profile,
    validate_seed,
)
from gpt_reg.models import BrowserHandoff, SignupRequest
from gpt_reg.phases.browser import otp as otp_mod


# ─── Datadog trace headers (thiếu là OTP bị silent-drop) ─────────────────


def _datadog_trace_headers() -> dict[str, str]:
    trace_id = str(random.getrandbits(64))
    parent_id = str(random.getrandbits(64))
    trace_hex = format(int(trace_id), "016x")
    parent_hex = format(int(parent_id), "016x")
    return {
        "traceparent": f"00-0000000000000000{trace_hex}-{parent_hex}-01",
        "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum",
        "x-datadog-parent-id": parent_id,
        "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": trace_id,
    }


# ─── Cookie helpers (tránh CookieConflict của curl_cffi) ──────────────────


def _domain_matches(cookie_domain: str | None, want: str) -> bool:
    dom = (cookie_domain or "").lower()
    w = want.lower().lstrip(".")
    if not dom:
        return False
    return dom == w or dom == f".{w}" or dom.endswith(f".{w}")


def _cookie_get(session, name: str, *, default: str = "", domain_preference: tuple[str, ...] = ()) -> str:
    for dom in domain_preference:
        try:
            val = session.cookies.get(name, domain=dom)
            if val:
                return val
        except Exception:
            pass
    ranked: list[tuple[int, str]] = []
    for ck in session.cookies.jar:
        if ck.name != name:
            continue
        rank = len(domain_preference)
        for i, want in enumerate(domain_preference):
            if _domain_matches(ck.domain, want):
                rank = i
                break
        ranked.append((rank, ck.value or ""))
    if ranked:
        ranked.sort(key=lambda item: item[0])
        return ranked[0][1]
    try:
        return session.cookies.get(name) or default
    except Exception:
        return default


def _cookie_has(session, name: str, *, domain_preference: tuple[str, ...] = ()) -> bool:
    return bool(_cookie_get(session, name, domain_preference=domain_preference))


# ─── Session factory + TLS rotation ──────────────────────────────────────


def _create_session(
    proxy: str | None,
    profile: Profile,
    *,
    fingerprint_seed: str,
):
    """Tạo session với TLS khớp `profile`, và ĐÍNH profile vào session.

    Mọi hàm dựng header đọc profile từ chính session, nên không thể xảy ra
    chuyện handshake dùng Chrome 131 còn header khai Chrome 145.
    """
    seed = validate_seed(fingerprint_seed)
    canonical = get_profile(profile.name)
    session = curl_requests.Session(impersonate=canonical.impersonate)
    session.trust_env = False
    if proxy:
        normalized = proxy
        if proxy.startswith("socks5://"):
            normalized = "socks5h://" + proxy[len("socks5://"):]
        session.proxies = {"https": normalized, "http": normalized}
    else:
        session.proxies = {"https": "", "http": ""}
    device_id = device_id_for_seed(seed, "http")
    for domain in (".chatgpt.com", ".openai.com"):
        session.cookies.set("oai-did", device_id, domain=domain, path="/")
    session.gpt_profile = canonical
    session.gpt_fingerprint_seed = seed
    return session


def _profile_of(session) -> Profile:
    profile = getattr(session, "gpt_profile", None)
    if not isinstance(profile, Profile):
        raise RuntimeError("HTTP session is missing fingerprint profile")
    return get_profile(profile.name)


def _seed_of(session) -> str:
    seed = getattr(session, "gpt_fingerprint_seed", None)
    if not isinstance(seed, str):
        raise RuntimeError("HTTP session is missing fingerprint seed")
    return validate_seed(seed)


def _is_tls_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    markers = ("curl: (35)", "tls connect error", "openssl_internal", "sslerror",
               "curl: (56)", "curl: (7)", "ssl_error", "handshake")
    return any(m in msg for m in markers)


def _is_cf_block(exc: BaseException) -> bool:
    """Cloudflare 403 do TLS fingerprint bị flag — đổi fingerprint là qua."""
    return isinstance(exc, HttpRegError) and getattr(exc, "step", None) == "cf_block"


# ─── Sentinel ─────────────────────────────────────────────────────────────


_SENTINEL_QUICKJS_ATTEMPTS = 3

# Chờ giữa các lần thử lại khi bị 429. Nhân với số lần đã thử.
RATE_LIMIT_BACKOFF_S = 30.0


def _sleep_cancellable(seconds: float, ctx: RunContext, step: str = "backoff") -> None:
    """Ngủ nhưng vẫn thoát nhanh khi user bấm Stop.

    `time.sleep(30)` nguyên khối làm nút Stop mất tác dụng suốt 30 giây.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if ctx.should_cancel is not None and ctx.should_cancel():
            raise JobCancelledError(step=step)
        time.sleep(min(0.5, deadline - time.monotonic()))


def _get_sentinel_token(session, device_id: str, flow: str, log: Callable, worker=None) -> str:
    """Generate one Sentinel token without silently changing implementation."""
    disable_quickjs = os.getenv("OPENAI_SENTINEL_DISABLE_QUICKJS", "0").lower() in (
        "1",
        "true",
        "yes",
    )
    if disable_quickjs:
        from gpt_reg.sentinel.pow import get_sentinel_token as _pow_token

        log("[sentinel] QuickJS disabled explicitly; using Python PoW")
        return _pow_token(session, device_id, flow=flow)

    from gpt_reg.sentinel.quickjs import get_sentinel_token_via_quickjs

    last_exc: Exception | None = None
    for attempt in range(1, _SENTINEL_QUICKJS_ATTEMPTS + 1):
        try:
            token = get_sentinel_token_via_quickjs(
                session, device_id, flow=flow, log=log, worker=worker
            )
            if not token:
                raise RuntimeError("QuickJS returned an empty Sentinel token")
            return token
        except Exception as exc:
            last_exc = exc
            log(f"[sentinel] QuickJS error {attempt}/{_SENTINEL_QUICKJS_ATTEMPTS}: {exc}")
        if attempt < _SENTINEL_QUICKJS_ATTEMPTS:
            time.sleep(1.5 * attempt)
            log(f"[sentinel] QuickJS retry {attempt + 1}/{_SENTINEL_QUICKJS_ATTEMPTS}")
    raise RuntimeError(
        f"QuickJS Sentinel failed after all attempts: {last_exc}"
    ) from last_exc


# ─── Common headers ────────────────────────────────────────────────────────


def _common_headers(session, referer: str = "https://chatgpt.com/") -> dict[str, str]:
    """Header cho request JSON — CHỈ phần theo ngữ cảnh.

    Không set User-Agent / sec-ch-ua / Accept-Language / Sec-Fetch-*: curl_cffi
    đã gửi đúng bộ đó theo `impersonate`, kể cả thứ tự. Ghi đè là phá vân tay —
    đo thật: chrome131 + header mặc định = 200, + header tự dựng = 403.
    """
    origin = "https://chatgpt.com"
    try:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            origin = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    headers = {"Accept": "application/json", "Referer": referer, "Origin": origin}
    headers.update(_datadog_trace_headers())
    return headers


def _html_headers(session, referer: str) -> dict[str, str]:
    """Điều hướng HTML — chỉ Referer, để curl_cffi lo phần còn lại."""
    return {"Referer": referer}


# ─── State machine steps ────────────────────────────────────────────────────


def _prime_chatgpt_session(session, log: Callable) -> None:
    """GET /auth/login để Cloudflare set __cf_bm trước khi gọi /api/auth/csrf.

    Không prime thì csrf trả token nhưng KHÔNG set cookie __Host-next-auth.csrf-token
    → POST signin bị reject → cascade 409 invalid_state. Idempotent."""
    try:
        if any(c.name == "__cf_bm" for c in session.cookies.jar):
            return
    except Exception:
        pass
    log("[http] [1/10] prime chatgpt.com (GET /auth/login)")
    # Không set Sec-Fetch-*/Accept-Encoding/Connection ở đây: curl_cffi đã gửi
    # đúng bộ điều hướng document theo impersonate, thêm tay là lệch vân tay.
    headers = _html_headers(session, "https://chatgpt.com/")
    resp = None
    for attempt in range(3):
        resp = session.get("https://chatgpt.com/auth/login", headers=headers, timeout=30, allow_redirects=True)
        if resp.status_code == 403 and attempt < 2:
            wait = (attempt + 1) * 5
            log(f"[http] prime CF 403, retry {wait}s ({attempt + 1}/3)")
            time.sleep(wait)
            continue
        break
    if resp is not None and resp.status_code == 403:
        raise HttpRegError("prime CF 403 (fingerprint bị chặn)", step="cf_block")
    if resp is None or resp.status_code >= 400:
        raise HttpRegError(f"prime session failed: HTTP {resp.status_code if resp else '?'}", step="prime")


def _step_csrf(session, log: Callable) -> str:
    _prime_chatgpt_session(session, log)
    log("[http] [2/10] CSRF token")
    headers = _common_headers(session, "https://chatgpt.com/auth/login")
    resp = None
    for attempt in range(3):
        resp = session.get("https://chatgpt.com/api/auth/csrf", headers=headers, timeout=30)
        if resp.status_code == 403 and attempt < 2:
            wait = (attempt + 1) * 5
            log(f"[http] CSRF CF 403, retry {wait}s ({attempt + 1}/3)")
            time.sleep(wait)
            continue
        break
    if resp is not None and resp.status_code == 403:
        raise HttpRegError("CSRF CF 403 (fingerprint bị chặn)", step="cf_block")
    if resp is None or resp.status_code != 200:
        raise HttpRegError(f"CSRF failed: HTTP {resp.status_code if resp else '?'}", step="csrf")
    csrf = resp.json().get("csrfToken", "")
    if not csrf:
        raise HttpRegError("CSRF token missing", step="csrf")
    return csrf


def _step_auth_url(
    session,
    csrf_token: str,
    log: Callable,
    device_id: str = "",
    login_hint: str = "",
    screen_hint: str = "login_or_signup",
) -> str:
    log("[http] [3/10] authorize URL")
    headers = _common_headers(session, "https://chatgpt.com/auth/login")
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    params = {
        "prompt": "login",
        "ext-passkey-client-capabilities": "01001",
        "screen_hint": screen_hint,
    }
    if device_id:
        params["ext-oai-did"] = device_id
    if login_hint:
        params["login_hint"] = login_hint
    url = "https://chatgpt.com/api/auth/signin/openai?" + urlencode(params)
    resp = session.post(
        url, headers=headers,
        data={"csrfToken": csrf_token, "callbackUrl": "https://chatgpt.com/", "json": "true"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise HttpRegError(f"signin/openai HTTP {resp.status_code}", step="signin")
    try:
        payload = resp.json()
    except Exception as exc:
        raise HttpRegError(f"signin/openai non-JSON: {(resp.text or '')[:200]}", step="signin") from exc
    auth_url = payload.get("url", "") if isinstance(payload, dict) else ""
    if not auth_url or "auth.openai.com" not in auth_url:
        raise HttpRegError(
            f"signin/openai không trả auth.openai.com (CSRF/anti-bot): {str(auth_url)[:160]}",
            step="signin",
        )
    return auth_url


def _step_oauth_init(session, auth_url: str, log: Callable) -> tuple[str, str]:
    """GET authorize. Return (device_id, landing_url).

    Vì bootstrap truyền `login_hint=email`, server tự điều hướng tới màn đúng với
    trạng thái account: `/log-in/password` (đã tồn tại), `/create-account/password`
    (mới), `/email-verification` (chờ verify). Bắt URL đích để phân loại — nhờ đó
    không phải thử đăng ký rồi hứng `invalid_auth_step`.

    KHÔNG tự dựng header `Sec-Fetch-*`: curl_cffi đã phát đúng bộ điều hướng
    document theo `impersonate` (xem docs/ANTIBOT.md).
    """
    log("[http] [4/10] OAuth init")
    resp = session.get(
        auth_url,
        headers=_html_headers(session, "https://chatgpt.com/auth/login"),
        timeout=30,
        allow_redirects=True,
    )
    landing = str(getattr(resp, "url", "") or "")
    device_id = _cookie_get(
        session, "oai-did", domain_preference=(".openai.com", "auth.openai.com", ".chatgpt.com")
    )
    return (device_id or ""), landing


def classify_landing(landing_url: str) -> str:
    """`login` | `register` | `otp` | `unknown` từ URL đích của GET authorize."""
    url = (landing_url or "").lower()
    if "/log-in/password" in url:
        return "login"
    if "/create-account/password" in url:
        return "register"
    if "/email-verification" in url or "/email-otp" in url:
        return "otp"
    return "unknown"


def _bootstrap_with_profile_rotation(
    proxy: str | None,
    log: Callable,
    *,
    fingerprint_seed: str,
    preferred_profile: str | None = None,
    login_hint: str = "",
) -> tuple[Any, str, str, str]:
    """Bootstrap, xoay **cả bộ vân tay** khi bị CF chặn hoặc TLS lỗi.

    Return (session, device_id, landing_url, auth_url). `landing_url` cho biết
    server xếp account vào màn nào — dùng `classify_landing()` để định tuyến.
    `auth_url` giữ lại để chạy lại authorize khi phiên đã xác thực nhưng
    continue_url không dẫn tới callback có `code=`.

    Xoay cả bộ (TLS + UA + client hints) chứ không riêng TLS: đổi mỗi TLS sẽ tạo
    ra session khai Chrome 145 trong header nhưng bắt tay như Chrome 131.
    """
    seed = validate_seed(fingerprint_seed)
    candidates = candidate_profiles(seed, preferred_profile)
    device_id = device_id_for_seed(seed, "http")
    last_exc: BaseException | None = None
    for idx, profile in enumerate(candidates):
        session = _create_session(
            proxy=proxy,
            profile=profile,
            fingerprint_seed=seed,
        )
        try:
            if idx > 0:
                log(f"[http] đổi vân tay → {profile.name} ({profile.impersonate})")
            csrf = _step_csrf(session, log)
            auth_url = _step_auth_url(session, csrf, log, device_id=device_id, login_hint=login_hint)
        except Exception as exc:
            last_exc = exc
            try:
                session.close()
            except Exception:
                pass
            if _is_tls_error(exc) or _is_cf_block(exc):
                if idx < len(candidates) - 1:
                    continue
                break
            raise

        # An authorize URL owns server-side auth state. Do not switch persona
        # after this boundary; report the actual error to the caller instead.
        try:
            oauth_did, landing = _step_oauth_init(session, auth_url, log)
        except Exception:
            try:
                session.close()
            except Exception:
                pass
            raise
        if oauth_did and oauth_did != device_id:
            try:
                session.close()
            except Exception:
                pass
            raise HttpRegError("OAuth device ID does not match fingerprint identity", step="identity")
        return session, device_id, landing, auth_url
    if last_exc and _is_cf_block(last_exc):
        raise HttpRegError("Cloudflare 403 với mọi fingerprint — đổi proxy/thử lại sau", step="cf_block") from last_exc
    if last_exc and _is_tls_error(last_exc):
        raise HttpRegError("TLS handshake fail với mọi fingerprint — đổi proxy", step="bootstrap") from last_exc
    if last_exc:
        raise last_exc
    raise HttpRegError("bootstrap failed", step="bootstrap")


def _step_authorize_continue(
    session, email: str, device_id: str, log: Callable, worker=None
) -> dict:
    """POST authorize/continue — đưa email vào state machine.

    **KHÔNG dùng trong luồng đăng ký.** Browser không gọi bước này (đã bắt
    request để xác nhận), và gọi nó khiến server chuyển sang nhánh
    `email_otp_verification` (passwordless) — lúc đó `user/register` bị từ chối
    bằng 400 `invalid_auth_step`. Giữ lại cho các luồng đăng nhập/khôi phục
    sau này có thể cần.
    """
    log("[http] [3.5/9] authorize/continue (đưa email vào state machine)")
    sentinel = _get_sentinel_token(session, device_id, "authorize_continue", log, worker=worker)
    headers = _common_headers(session, "https://auth.openai.com/create-account")
    headers["Content-Type"] = "application/json"
    if sentinel:
        headers["openai-sentinel-token"] = sentinel
    if device_id:
        headers["oai-device-id"] = device_id
    resp = session.post(
        "https://auth.openai.com/api/accounts/authorize/continue",
        headers=headers,
        json={"username": {"value": email, "kind": "email"}, "screen_hint": "signup"},
        timeout=30,
    )
    body = resp.text or ""
    if resp.status_code == 409 and "invalid_state" in body:
        raise HttpRegError("authorize/continue 409 invalid_state", step="invalid_state")
    if resp.status_code != 200:
        raise HttpRegError(
            f"authorize/continue HTTP {resp.status_code} - {body[:200]}", step="authorize_continue"
        )
    try:
        data = resp.json()
    except Exception:
        data = {}
    page_type = ((data.get("page") or {}).get("type") or "").strip() if isinstance(data, dict) else ""
    log(f"[http] authorize/continue OK → page_type={page_type!r}")
    return data if isinstance(data, dict) else {}


def _step_login_password(
    session, password: str, device_id: str, log: Callable, worker=None
) -> dict:
    """Đăng nhập bằng mật khẩu đã biết. Dùng cho account ĐÃ đăng ký.

    Khác `user/register`: bước này **CÓ** gửi sentinel, với `flow="login"`.
    Bootstrap đã truyền `login_hint=email` nên server tự đưa phiên tới
    `/log-in/password`, không cần gọi `authorize/continue`.

    Return dict `{page_type, continue_url, _ok, _status, _body}`.
    """
    log("[http] [L1] đăng nhập bằng mật khẩu")
    sentinel = _get_sentinel_token(session, device_id, "login", log, worker=worker)
    headers = _common_headers(session, "https://auth.openai.com/log-in/password")
    headers["Content-Type"] = "application/json"
    if sentinel:
        headers["openai-sentinel-token"] = sentinel
    if device_id:
        headers["oai-device-id"] = device_id
    resp = session.post(
        "https://auth.openai.com/api/accounts/password/verify",
        headers=headers,
        json={"password": password},
        timeout=30,
    )
    body = resp.text or ""
    if resp.status_code != 200:
        low = body.lower()
        rejected = resp.status_code == 401 or any(
            m in low
            for m in (
                "incorrect password",
                "incorrect email address or password",
                "invalid password",
                "wrong password",
                "invalid credentials",
            )
        )
        step = "wrong_password" if rejected else "login"
        raise HttpRegError(
            f"password/verify HTTP {resp.status_code} - {body[:200]}", step=step
        )
    try:
        data = resp.json() or {}
    except Exception:
        data = {}
    page_type = ((data.get("page") or {}).get("type") or "").strip()
    continue_url = (data.get("continue_url") or "").strip()
    log(f"[http] mật khẩu đúng → page_type={page_type!r}")
    return {"page_type": page_type, "continue_url": continue_url}


def _saved_session_field(email: str, field: str) -> str | None:
    """Đọc một field trong `runtime/sessions/<email>.json` của lần chạy trước.

    Nguồn duy nhất cho hai thứ mà combo KHÔNG có:

      - `password`: mật khẩu **tài khoản ChatGPT** do tool tự sinh
        (`secrets.token_urlsafe`). Mật khẩu trong combo là của **hộp thư
        Hotmail** — hai thứ khác nhau, nộp mật khẩu mail vào `password/verify`
        thì 401 `invalid_username_or_password` (đo thật trên
        BraunbergerKlare74@hotmail.com: combo 9 ký tự, account 12 ký tự).
      - `mfa_secret`: TOTP secret từ lúc enroll, cần để vượt `mfa_challenge`.
    """
    try:
        from gpt_reg.config import load_settings

        safe = email.replace("@", "_at_").replace(".", "_")
        path = load_settings().sessions_dir / f"{safe}.json"
        if not path.exists():
            return None
        value = (json.loads(path.read_text(encoding="utf-8")) or {}).get(field)
        return str(value) if value else None
    except Exception:
        return None


def _saved_mfa_secret(email: str) -> str | None:
    return _saved_session_field(email, "mfa_secret")


def mfa_challenge_id(challenge_url: str) -> str:
    """Id của challenge nằm cuối `/mfa-challenge/<id>`.

    `mfa/verify` bắt buộc có field `id` này; thiếu thì 400
    `missing_required_parameter` (đã đo bằng `test/probe_mfa_verify.py`).
    """
    url = (challenge_url or "").split("?")[0].rstrip("/")
    if "/mfa-challenge/" not in url:
        return ""
    return url.rsplit("/", 1)[-1]


def _step_mfa_challenge(
    session, secret: str, challenge_url: str, device_id: str, log: Callable
) -> str:
    """Vượt màn `mfa_challenge` bằng TOTP. Return continue_url (có `code=`).

    Payload đúng là `{"type": "totp", "code": ..., "id": <challenge_id>}` — dò
    bằng `test/probe_mfa_verify.py`, server báo thiếu field theo từng bước
    (`type` trước, rồi `id`). Trả về thẳng callback URL kèm `code=`, nên không
    cần chạy lại authorize sau bước này.
    """
    from gpt_reg import totp_helper

    challenge_id = mfa_challenge_id(challenge_url)
    if not challenge_id:
        raise HttpRegError(
            f"không lấy được id challenge từ {challenge_url[:100]!r}", step="mfa"
        )

    # Sinh code ở đầu cửa sổ 30s: code còn <5s sẽ hết hạn giữa đường bay.
    remaining = totp_helper.time_remaining()
    if remaining < 5:
        log(f"[http] TOTP còn {remaining}s → chờ cửa sổ mới")
        time.sleep(remaining + 0.5)
    code = totp_helper.generate_code(secret)
    log("[http] [L2] vượt 2FA bằng TOTP đã lưu")
    headers = _common_headers(session, challenge_url or "https://auth.openai.com/mfa-challenge")
    headers["Content-Type"] = "application/json"
    if device_id:
        headers["oai-device-id"] = device_id
    resp = session.post(
        "https://auth.openai.com/api/accounts/mfa/verify",
        headers=headers,
        json={"type": "totp", "code": code, "id": challenge_id},
        timeout=30,
    )
    if resp.status_code != 200:
        raise HttpRegError(
            f"mfa/verify HTTP {resp.status_code} - {(resp.text or '')[:200]}", step="mfa"
        )
    try:
        data = resp.json() or {}
    except Exception:
        data = {}
    return (data.get("continue_url") or "").strip()


def _step_send_otp(session, device_id: str, log: Callable) -> None:
    log("[http] send OTP")
    headers = _common_headers(session, "https://auth.openai.com/create-account/password")
    if device_id:
        headers["oai-device-id"] = device_id
    resp = session.get("https://auth.openai.com/api/accounts/email-otp/send", headers=headers, timeout=30)
    if resp.status_code != 200:
        headers2 = _common_headers(session, "https://auth.openai.com/create-account/password")
        headers2["Content-Type"] = "application/json"
        if device_id:
            headers2["oai-device-id"] = device_id
        resp2 = session.post(
            "https://auth.openai.com/api/accounts/passwordless/send-otp", headers=headers2, timeout=30,
        )
        if resp2.status_code != 200:
            raise HttpRegError(
                f"OTP send failed: primary={resp.status_code} fallback={resp2.status_code}", step="otp_send"
            )


def _step_resend_otp(session, device_id: str, log: Callable) -> bool:
    headers = _common_headers(session, "https://auth.openai.com/email-verification")
    headers["Content-Type"] = "application/json"
    if device_id:
        headers["oai-device-id"] = device_id
    resp = session.post("https://auth.openai.com/api/accounts/email-otp/resend", headers=headers, timeout=30)
    if resp.status_code == 200:
        log("[http] OTP resent")
        return True
    log(f"[http] OTP resend HTTP {resp.status_code}")
    return False


def _send_initial_otp(
    session,
    device_id: str,
    log: Callable,
    *,
    cold_passwordless: bool,
    reg_continue: str,
) -> None:
    """Phát đúng loại OTP theo state hiện tại.

    Vào thẳng màn OTP từ authorize là passwordless login. Browser bấm Resend
    và nhận *login code*; gọi endpoint Send lại tạo *verification code* và đẩy
    account sang onboarding/about-you dù mã vẫn validate 200.
    """
    if cold_passwordless:
        if not _step_resend_otp(session, device_id, log):
            raise HttpRegError(
                "không resend được login OTP cho phiên passwordless", step="otp_send"
            )
        return
    if reg_continue and "/email-otp/send" in reg_continue:
        otp_headers = _common_headers(session, "https://auth.openai.com/email-verification")
        if device_id:
            otp_headers["oai-device-id"] = device_id
        response = session.get(reg_continue, headers=otp_headers, timeout=30)
        if response.status_code not in (200, 302):
            _step_send_otp(session, device_id, log)
        return
    _step_send_otp(session, device_id, log)


def _step_verify_otp(session, otp_code: str, device_id: str, log: Callable) -> dict:
    """Return dict kèm _ok/_status/_body — caller tự quyết retry."""
    log("[http] verify OTP")
    headers = _common_headers(session, "https://auth.openai.com/email-verification")
    headers["Content-Type"] = "application/json"
    if device_id:
        headers["oai-device-id"] = device_id
    resp = session.post(
        "https://auth.openai.com/api/accounts/email-otp/validate",
        headers=headers, json={"code": otp_code}, timeout=30,
    )
    if resp.status_code != 200:
        return {"_ok": False, "_status": resp.status_code, "_body": resp.text or ""}
    try:
        data = resp.json()
    except Exception:
        data = {}
    if isinstance(data, dict):
        data["_ok"] = True
        data["_status"] = 200
    return data


def _safe_error_detail(body: str, *, limit: int = 240) -> str:
    """Rút gọn lỗi upstream để log được nguyên nhân mà không lộ email/token."""
    raw = str(body or "").strip()
    code = ""
    message = ""
    try:
        data = json.loads(raw)
        error = data.get("error", data) if isinstance(data, dict) else {}
        if isinstance(error, dict):
            code = str(error.get("code") or error.get("type") or "").strip()
            message = str(error.get("message") or error.get("detail") or "").strip()
    except Exception:
        pass
    text = ": ".join(part for part in (code, message) if part) or raw
    text = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[email]", text, flags=re.I)
    text = re.sub(
        r"(?i)\b(token|secret|refresh_token)\s*[=:]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}=[redacted]",
        text,
    )
    text = " ".join(text.split())
    return text[:limit] or "no error detail"


def _classify_otp_failure(status: int, body: str) -> str:
    normalized = str(body or "").lower()
    if status == 401 or "wrong_email_otp_code" in normalized or "wrong code" in normalized:
        return "wrong_code"
    if status == 409 and ("invalid_state" in normalized or "session" in normalized):
        return "invalid_state"
    if status == 429 or "rate_limit" in normalized:
        return "rate_limit"
    return "other"


def _step_create_account(
    session, name: str, birthdate: str, device_id: str, log: Callable,
    sentinel_token: str | None = None, worker=None,
) -> str:
    log("[http] create_account")
    sentinel = sentinel_token or _get_sentinel_token(session, device_id, "create_account", log, worker=worker)
    headers = _common_headers(session, "https://auth.openai.com/about-you")
    headers["Content-Type"] = "application/json"
    if sentinel:
        headers["openai-sentinel-token"] = sentinel
    if device_id:
        headers["oai-device-id"] = device_id
    resp = session.post(
        "https://auth.openai.com/api/accounts/create_account",
        headers=headers, json={"name": name, "birthdate": birthdate}, timeout=30,
    )
    if resp.status_code == 400 and "user_already_exists" in (resp.text or ""):
        # Account đã đủ hồ sơ rồi (đường retry acc cũ). Server không coi đây là
        # ngõ cụt: nó kèm sẵn `redirect_uri` trong body lỗi — đúng chỗ để đi
        # tiếp và lấy session. Đo thật trên TouchRockett622@hotmail.com.
        try:
            err = (resp.json() or {}).get("error") or {}
        except Exception:
            err = {}
        redirect = str(err.get("redirect_uri") or "").strip()
        if redirect:
            log("[http] account đã đủ hồ sơ → đi tiếp bằng redirect_uri của server")
            return redirect
        raise HttpRegError(
            "account đã tồn tại nhưng server không trả redirect_uri để đi tiếp",
            step="create_account",
        )
    if resp.status_code != 200:
        raise HttpRegError(f"create_account HTTP {resp.status_code} - {(resp.text or '')[:400]}", step="create_account")
    continue_url = (resp.json().get("continue_url") or "").strip()
    if not continue_url:
        raise HttpRegError("create_account: no continue_url", step="create_account")
    return continue_url


def _step_follow_redirects(session, start_url: str, log: Callable, should_cancel=None) -> str:
    log(f"[http] [10/10] follow redirects ← {start_url.split('?')[0][:70]}")
    current = start_url
    callback_url = ""
    for _ in range(12):
        # Kiểm huỷ mỗi hop: chuỗi có thể tới 12 GET × 30s. Không có điểm dừng thì
        # Stop mất tới vài phút mới thoát.
        if should_cancel is not None and should_cancel():
            raise JobCancelledError(step="http_redirect")
        if "/api/auth/callback/openai" in current and "code=" in current:
            callback_url = current
            break
        resp = session.get(current, headers=_html_headers(session, "https://chatgpt.com/"), timeout=30, allow_redirects=False)
        if resp.status_code not in (301, 302, 303, 307, 308):
            # Dừng ở một trang 200: ghi lại điểm dừng, nếu không thì
            # "callback=missing" không nói được chuỗi tắt ở đâu.
            log(f"[http] chuỗi dừng: HTTP {resp.status_code} tại {current.split('?')[0][:70]}")
        if resp.status_code in (301, 302, 303, 307, 308):
            location = (resp.headers.get("Location") or "").strip()
            if not location:
                break
            if location.startswith("/"):
                parsed = urlparse(current)
                location = f"{parsed.scheme}://{parsed.netloc}{location}"
            current = location
            if "/api/auth/callback/openai" in location and "code=" in location:
                callback_url = location
                break
        else:
            break
    log(f"[http] redirect chain done, callback={'found' if callback_url else 'missing'}")
    return callback_url


def _consume_callback(session, callback_url: str, log: Callable) -> bool:
    if not callback_url or "code=" not in callback_url:
        return False
    try:
        session.get(callback_url, headers=_html_headers(session, "https://auth.openai.com/"), timeout=30, allow_redirects=True)
        for name in (
            "__Secure-next-auth.session-token",
            "__Secure-next-auth.session-token.0",
            "__Secure-next-auth.session-token.1",
        ):
            if _cookie_has(session, name, domain_preference=(".chatgpt.com",)):
                return True
        return False
    except Exception as exc:
        log(f"[http] consume callback lỗi: {exc}")
        return False


def _get_session_tokens(session, log: Callable) -> tuple[str, str, str, str]:
    resp = session.get("https://chatgpt.com/api/auth/session", headers=_common_headers(session, "https://chatgpt.com/"), timeout=30)
    if resp.status_code != 200:
        log(f"[http] /api/auth/session HTTP {resp.status_code}")
        return "", "", "", ""
    data = resp.json() or {}
    access_token = data.get("accessToken", "") or ""
    user = data.get("user", {}) or {}
    user_id = user.get("id", "") or ""
    authenticated_email = str(user.get("email", "") or "").strip()
    session_token = _cookie_get(session, "__Secure-next-auth.session-token", domain_preference=(".chatgpt.com",))
    return session_token, access_token, user_id, authenticated_email


def _extract_cookies(session) -> list[dict[str, Any]]:
    """Dump toàn bộ cookie jar cho handoff.

    Phải duyệt `session.cookies.jar` chứ KHÔNG phải `session.cookies`: trong
    curl_cffi, `Cookies` là MutableMapping[str, str] nên `__iter__` trả về **tên**
    cookie (str), không phải object — duyệt nhầm thì mọi `.name`/`.value` đều rỗng
    và hàm luôn trả list rỗng, khiến phase-2 không tìm thấy session-token.
    """
    cookies: list[dict[str, Any]] = []
    try:
        for cookie in session.cookies.jar:
            name = getattr(cookie, "name", "") or ""
            value = getattr(cookie, "value", "") or ""
            if name and value:
                cookies.append({
                    "name": name,
                    "value": value,
                    "domain": getattr(cookie, "domain", "") or "",
                    "path": getattr(cookie, "path", "") or "/",
                    "secure": bool(getattr(cookie, "secure", True)),
                })
    except Exception:
        pass
    return cookies


# ─── OTP: poll qua Graph + verify với resend ────────────────────────────────


def _poll_otp(mail: MailProvider, request: SignupRequest, since: datetime, consumed: set[str],
              should_cancel, log: Callable) -> tuple[str, float]:
    """Chạy async poll_code (tái dùng slicing + cancel + retry SSL) trong sync core."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            otp_mod.poll_code(
                mail,
                email=request.email,
                since=since,
                timeout_s=request.otp_timeout_seconds,
                poll_interval_s=request.otp_poll_interval_seconds,
                log=log,
                consumed=consumed,
                should_cancel=should_cancel,
            )
        )
    finally:
        loop.close()


# ─── Orchestrator (sync core chạy trong thread) ─────────────────────────────


def _run_sync(ctx: RunContext, request: SignupRequest, mail: MailProvider, worker, log: Callable) -> dict[str, Any]:
    # Mọi session tạo ra (kể cả các lần re-bootstrap) được đóng ở finally.
    sessions: list[Any] = []
    try:
        result = _run_flow(ctx, request, mail, worker, log, sessions)
        profile = _profile_of(result["session"])
        result.update(
            user_agent=profile.user_agent,
            impersonate=profile.impersonate,
            fingerprint_profile=profile.name,
        )
        return result
    finally:
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass


def _run_flow(
    ctx: RunContext, request: SignupRequest, mail: MailProvider, worker, log: Callable, sessions: list[Any]
) -> dict[str, Any]:
    def _cancel_point(step: str) -> None:
        if ctx.should_cancel is not None and ctx.should_cancel():
            raise JobCancelledError(step=step)

    password = request.password or secrets.token_urlsafe(12)[:16]
    session = None
    device_id = ""
    auth_url = ""
    reg_continue = ""
    otp_seconds = 0.0
    # login_mode: account đã tồn tại → đi đường đăng nhập thay vì đăng ký.
    login_mode = False
    login_kind = ""          # "password" | "otp"
    needs_otp_after_login = False
    needs_details = False    # account nửa chừng → vẫn phải gọi create_account

    # Bootstrap + register, retry re-bootstrap trên 409 invalid_state.
    max_register_attempts = 3
    for register_attempt in range(1, max_register_attempts + 1):
        _cancel_point("http_register")
        if register_attempt > 1:
            log(f"[http] re-bootstrap (lần {register_attempt}/{max_register_attempts}) sau 409 invalid_state")

        session, device_id, landing, auth_url = _bootstrap_with_profile_rotation(
            request.proxy,
            log,
            fingerprint_seed=request.fingerprint_seed,
            preferred_profile=request.fingerprint_profile,
            login_hint=request.email,
        )
        sessions.append(session)

        # Server đã tự xếp account vào màn tương ứng nhờ `login_hint`. Đọc URL
        # đích để đi đúng nhánh ngay, thay vì gọi register rồi hứng
        # `invalid_auth_step` (tốn một vòng và một lần sentinel).
        landing_kind = classify_landing(landing)
        log(f"[http] landing={landing_kind} ({landing.split('?')[0][:70]})")
        if landing_kind == "login":
            # Màn nhập mật khẩu → xác thực bằng mật khẩu đã biết.
            log("[http] [5/10] identify existing account (password)")
            log(f"[http] {request.email} đã có tài khoản → đăng nhập bằng mật khẩu")
            login_mode = True
            login_kind = "password"
            break
        if landing_kind == "otp":
            # Màn verify email → server muốn MÃ, không phải mật khẩu. Gọi
            # `password/verify` ở trạng thái này bị 409 invalid_state (đo thật).
            # Đây cũng là trạng thái của account đăng ký nửa chừng.
            log("[http] [5/10] identify existing account (OTP)")
            log(f"[http] {request.email} cần verify email → đăng nhập bằng mã OTP")
            login_mode = True
            login_kind = "otp"
            needs_otp_after_login = True
            reg_continue = landing
            break

        # Bám sát đúng những gì browser làm (bắt được bằng
        # `test/probe_browser_capture.py`), vì đó là biến thể duy nhất server
        # chấp nhận — `test/probe_register_variants.py` đã thử 4 biến thể:
        #
        #   1. KHÔNG gọi `authorize/continue`. Nút "Continue with password" chỉ
        #      đổi route SPA phía client, không gọi API nào.
        #   2. GET `/create-account/password` bằng **header HTML** (điều hướng
        #      trang), không phải header JSON.
        #   3. Request register **KHÔNG gửi** `openai-sentinel-token` — browser
        #      không gửi, và gửi vào thì server trả 400 invalid_auth_step.
        try:
            session.get(
                "https://auth.openai.com/create-account/password",
                headers=_html_headers(session, "https://auth.openai.com/email-verification"),
                timeout=20,
            )
        except Exception:
            pass

        log(f"[http] [5/10] register account (email={request.email})")
        reg_headers = _common_headers(session, "https://auth.openai.com/create-account/password")
        reg_headers["Content-Type"] = "application/json"
        if device_id:
            reg_headers["oai-device-id"] = device_id
        resp = session.post(
            "https://auth.openai.com/api/accounts/user/register",
            headers=reg_headers, json={"password": password, "username": request.email}, timeout=30,
        )
        if resp.status_code == 200:
            reg_data = resp.json() or {}
            reg_continue = (reg_data.get("continue_url") or "").strip()
            page_type = ((reg_data.get("page") or {}).get("type") or "").strip()
            log(f"[http] register OK → page_type={page_type!r}")
            # Account đã tồn tại trên server từ giây này. Ghi mật khẩu ngay,
            # trước OTP — flow còn nhiều bước có thể hỏng.
            ctx.account_created(password)
            break

        body = (resp.text or "")[:300]
        # 429 là tạm thời: chờ rồi thử lại, đừng đốt combo. Gặp thật khi chạy
        # nhiều luồng hoặc dò liên tục trên cùng một proxy.
        if resp.status_code == 429:
            if register_attempt >= max_register_attempts:
                raise HttpRegError(
                    f"rate limit sau {max_register_attempts} lần — giảm số luồng "
                    f"hoặc đổi proxy: {body[:160]}",
                    step="rate_limit",
                )
            backoff = RATE_LIMIT_BACKOFF_S * register_attempt
            log(f"[http] 429 rate limit — chờ {backoff:.0f}s rồi thử lại")
            _sleep_cancellable(backoff, ctx)
            continue
        if resp.status_code == 400 and "invalid_auth_step" in (resp.text or ""):
            # Chuỗi request đã đúng (đã đo bằng probe_register_variants), nên
            # `invalid_auth_step` nghĩa là email ĐÃ có tài khoản. Đăng ký lại là
            # vô nghĩa — chuyển sang ĐĂNG NHẬP để cứu account, đây cũng chính là
            # đường retry cho những acc từng fail giữa chừng.
            log(f"[http] {request.email} đã có tài khoản → chuyển sang đăng nhập")
            login_mode = True
            break
        if resp.status_code == 409 and "invalid_state" in body:
            if register_attempt >= max_register_attempts:
                raise HttpRegError(f"user/register 409 invalid_state sau {max_register_attempts} lần: {body}", step="register")
            _sleep_cancellable(1.5 + random.uniform(0, 1.0), ctx)  # jitter tránh đồng pha
            continue
        raise HttpRegError(f"user/register HTTP {resp.status_code} - {body}", step="register")

    if login_mode and login_kind == "password":
        _cancel_point("http_login")
        # Mật khẩu đã biết (từ combo, hoặc đã ghi ở lần chạy trước nhờ
        # `account_created`). Bootstrap dùng `login_hint=email` nên server đã đưa
        # phiên tới /log-in/password.
        # Ưu tiên mật khẩu ACCOUNT đã lưu ở lần chạy trước. `request.password`
        # đến từ combo = mật khẩu HỘP THƯ, không phải mật khẩu ChatGPT.
        saved_password = _saved_session_field(request.email, "password")
        candidates = [p for p in (saved_password, password) if p]
        # Loại trùng, giữ nguyên thứ tự — tránh nộp cùng một mật khẩu hai lần
        # (mỗi lần sai là một lần đốt sentinel và một nhịp rate limit).
        seen_pw: set[str] = set()
        candidates = [p for p in candidates if not (p in seen_pw or seen_pw.add(p))]

        login = None
        last_exc: HttpRegError | None = None
        for idx, candidate in enumerate(candidates, 1):
            which = "đã lưu" if candidate == saved_password else "từ combo"
            if len(candidates) > 1:
                log(f"[http] thử mật khẩu {which} ({idx}/{len(candidates)})")
            try:
                login = _step_login_password(session, candidate, device_id, log, worker=worker)
                password = candidate  # ghi lại để export đúng mật khẩu thật
                break
            except HttpRegError as exc:
                if exc.step != "wrong_password" or idx >= len(candidates):
                    raise
                log(f"[http] mật khẩu {which} bị từ chối → thử mật khẩu còn lại")
                last_exc = exc
        if login is None:
            raise last_exc or HttpRegError("không đăng nhập được", step="login")

        page_type = login.get("page_type") or ""
        reg_continue = login.get("continue_url") or ""

        # Account từng bật 2FA → server chặn ở `mfa_challenge`. Secret nằm sẵn
        # trong session file từ lúc enroll, nên đây vẫn cứu được.
        if page_type == "mfa_challenge" or "/mfa-challenge" in reg_continue:
            _cancel_point("http_mfa")
            secret = _saved_mfa_secret(request.email)
            if not secret:
                raise HttpRegError(
                    f"{request.email} đã bật 2FA nhưng không tìm thấy mfa_secret "
                    f"trong session file — không vượt được mfa_challenge",
                    step="mfa",
                )
            reg_continue = (
                _step_mfa_challenge(session, secret, reg_continue, device_id, log)
                or reg_continue
            )
            page_type = ""

        if page_type in ("email_otp_verification", "email_verification") or (
            "/email-verification" in reg_continue
        ):
            # Account đăng ký nửa chừng: có mật khẩu nhưng chưa verify email.
            log("[http] cần verify email sau khi đăng nhập")
            needs_otp_after_login = True
        else:
            needs_otp_after_login = False

    # Đăng nhập xong mà không cần verify email → nhảy thẳng tới lấy session,
    # bỏ qua cả OTP và create_account (account đã có profile từ trước).
    if login_mode and not needs_otp_after_login:
        _cancel_point("http_login_finish")
        log("[http] đã có phiên đăng nhập — bỏ qua OTP và create_account")
        log("[http] [6/10] skipped: existing authenticated session")
        log("[http] [7/10] skipped: OTP wait not required")
        log("[http] [8/10] skipped: OTP verify not required")
        log("[http] [9/10] skipped: existing account profile")
        callback_url = _step_follow_redirects(
            session, reg_continue or "https://chatgpt.com/", log,
            should_cancel=ctx.should_cancel,
        )
        if callback_url:
            _consume_callback(session, callback_url, log)
        session_token, access_token, user_id, authenticated_email = _get_session_tokens(
            session, log
        )
        if not session_token and not access_token and auth_url:
            # continue_url không dẫn tới callback có `code=` → chạy lại authorize
            # để server phát code cho phiên vừa xác thực (xem chú thích dưới).
            log("[http] continue_url không ra code → chạy lại authorize")
            callback_url = _step_follow_redirects(
                session, auth_url, log, should_cancel=ctx.should_cancel
            )
            if callback_url:
                _consume_callback(session, callback_url, log)
            session_token, access_token, user_id, authenticated_email = _get_session_tokens(
                session, log
            )
        if not session_token and not access_token:
            raise HttpRegError(
                "đăng nhập xong nhưng không lấy được session_token/access_token",
                step="session",
            )
        return {
            "cookies": _extract_cookies(session),
            "callback_url": callback_url,
            "otp_seconds": otp_seconds,
            "access_token": access_token or None,
            "user_id": user_id or None,
            "authenticated_email": authenticated_email or request.email,
            "account_password": password,
            "recovered": True,
            "registration_outcome": "account_exists",
            "session": session,
        }

    # Send OTP. otp_since = thời điểm gửi để lọc mail cũ.
    otp_since = datetime.now(timezone.utc)
    _send_initial_otp(
        session,
        device_id,
        log,
        cold_passwordless=login_mode and login_kind == "otp",
        reg_continue=reg_continue,
    )
    log("[http] [6/10] OTP sent")

    # Pre-compute sentinel create_account song song với lúc chờ OTP.
    #
    # Thread dùng SESSION RIÊNG, không mượn session chính. `curl_cffi.Session`
    # không thread-safe: nếu join hết 45s mà thread còn chạy, hoặc job bị huỷ,
    # thì `finally` của `_run_sync` sẽ đóng session ngay dưới một request đang
    # bay — hỏng cả process chứ không riêng job. Sentinel gọi
    # `sentinel.openai.com` (host khác, không cần cookie auth) nên tách session
    # là an toàn; chỉ cần giữ đúng profile vân tay.
    precomputed: dict[str, str | None] = {"token": None}
    pre_profile = _profile_of(session)

    def _precompute() -> None:
        # Thread tạo VÀ đóng session của chính nó — không đưa vào `sessions`,
        # vì `finally` bên ngoài có thể chạy trước khi thread xong.
        own = _create_session(
            request.proxy,
            pre_profile,
            fingerprint_seed=request.fingerprint_seed,
        )
        try:
            precomputed["token"] = _get_sentinel_token(
                own, device_id, "create_account", log, worker=worker
            )
        except Exception as exc:
            log(f"[http] pre-compute sentinel lỗi (sẽ tính lại): {exc}")
        finally:
            try:
                own.close()
            except Exception:
                pass

    pre_thread = threading.Thread(target=_precompute, name="http-precompute-sentinel", daemon=True)
    pre_thread.start()

    # Poll + verify với resend khi sai mã.
    consumed: set[str] = set()
    _cancel_point("http_otp")
    log("[http] [7/10] chờ OTP")
    otp_code, waited = _poll_otp(mail, request, otp_since, consumed, ctx.should_cancel, log)
    otp_seconds += waited
    pre_thread.join(timeout=45.0)
    if pre_thread.is_alive():
        # Thread còn chạy: bỏ token (có thể ghi dở) và tính lại ở create_account.
        # Session của nó do chính nó đóng nên không có chuyện đóng dưới chân.
        log("[http] pre-compute sentinel quá 45s — sẽ tính lại tại create_account")
        precomputed["token"] = None

    max_verify = 3
    verified = False
    log("[http] [8/10] verify OTP")
    for v_attempt in range(1, max_verify + 1):
        _cancel_point("http_verify")
        otp_resp = _step_verify_otp(session, otp_code, device_id, log)
        if otp_resp.get("_ok"):
            verified = True
            break
        status = otp_resp.get("_status")
        body = str(otp_resp.get("_body") or "")
        failure_kind = _classify_otp_failure(int(status or 0), body)
        detail = _safe_error_detail(body)
        if failure_kind == "invalid_state":
            raise HttpRegError(
                f"OTP verify HTTP {status} - {detail}", step="invalid_state"
            )
        if failure_kind == "rate_limit":
            raise HttpRegError(
                f"OTP verify HTTP {status} - {detail}", step="rate_limit"
            )
        if failure_kind != "wrong_code":
            raise HttpRegError(f"OTP verify HTTP {status} - {detail}", step="verify")
        if v_attempt >= max_verify:
            raise HttpRegError(f"OTP verify sai sau {max_verify} lần (HTTP {status})", step="verify")
        log(f"[http] OTP sai (lần {v_attempt}/{max_verify}) → resend + poll mã mới")
        resend_since = datetime.now(timezone.utc)
        try:
            if not _step_resend_otp(session, device_id, log):
                _step_send_otp(session, device_id, log)
        except Exception as exc:
            log(f"[http] resend lỗi (vẫn poll): {exc}")
        otp_code, waited = _poll_otp(mail, request, resend_since, consumed, ctx.should_cancel, log)
        otp_seconds += waited

    if not verified:
        raise HttpRegError("OTP verify thất bại", step="verify")

    # create_account → redirects → callback → session.
    #
    # Account đã tồn tại có hai trạng thái: đủ hồ sơ (name/birthdate) hoặc đăng
    # ký nửa chừng. KHÔNG đoán được bằng phản hồi của bước verify: đo thật trên
    # TouchRockett622@hotmail.com, server trả `page.type='about_you'` nhưng
    # create_account lại 400 `user_already_exists` — hai tín hiệu mâu thuẫn.
    #
    # Nên đi theo hướng ngược lại: thử lấy session bằng continue_url trước (rẻ,
    # không tốn sentinel), chỉ gọi create_account khi KHÔNG ra được session.
    _cancel_point("http_finalize")
    if login_mode:
        log("[http] [9/10] resolve existing account profile")
        page = otp_resp.get("page")
        otp_page = ((page or {}).get("type") or "").strip().lower() if isinstance(page, dict) else ""
        continue_url = (otp_resp.get("continue_url") or "").strip() or "https://chatgpt.com/"
        log(f"[http] account cũ (page={otp_page!r}) — thử lấy session trước")
    else:
        log("[http] [9/10] create account profile")
        continue_url = _step_create_account(
            session, request.name, request.birthdate, device_id, log,
            sentinel_token=precomputed.get("token"), worker=worker,
        )
    def _finish(url: str) -> tuple[str, str, str, str]:
        # Kiểm huỷ trước và trong chuỗi redirect: tới 12 GET tuần tự × 30s.
        _cancel_point("http_redirect")
        cb = _step_follow_redirects(session, url, log, should_cancel=ctx.should_cancel)
        if cb:
            _consume_callback(session, cb, log)
        return (cb, *_get_session_tokens(session, log))  # type: ignore[return-value]

    callback_url, session_token, access_token, user_id, authenticated_email = _finish(continue_url)
    if not session_token and not access_token and login_mode:
        # Không ra session → account đăng ký nửa chừng, còn kẹt ở /about-you.
        # Đây chính là đường cứu những acc chết giữa flow ở lần chạy trước.
        # (create_account tự trả `redirect_uri` nếu account thật ra đã đủ hồ sơ.)
        needs_details = True
        log("[http] chưa có session → thử hoàn tất create_account")
        continue_url = _step_create_account(
            session, request.name, request.birthdate, device_id, log,
            sentinel_token=precomputed.get("token"), worker=worker,
        )
        callback_url, session_token, access_token, user_id, authenticated_email = _finish(continue_url)

    if not session_token and not access_token and auth_url:
        # Phiên auth.openai.com ĐÃ xác thực (vừa verify OTP xong) nhưng
        # continue_url không dẫn tới callback có `code=` — hay gặp ở account cũ,
        # nơi server trả về trang /about-you thay vì một OAuth continue thật.
        # Chạy lại chính authorize URL: server thấy phiên đã xác thực sẽ phát
        # code mới và đẩy thẳng về /api/auth/callback/openai.
        log("[http] continue_url không ra code → chạy lại authorize")
        callback_url, session_token, access_token, user_id, authenticated_email = _finish(auth_url)

    if not session_token and not access_token:
        raise HttpRegError("hoàn tất nhưng không lấy được session_token/access_token", step="session")

    return {
        "cookies": _extract_cookies(session),
        "callback_url": callback_url,
        "otp_seconds": otp_seconds,
        "access_token": access_token or None,
        "user_id": user_id or None,
        "authenticated_email": authenticated_email or request.email,
        "account_password": password,
        "registration_outcome": "account_exists" if login_mode else "success",
        "session": session,
    }


class HttpRegPhase:
    mode = "http"

    async def run(
        self,
        ctx: RunContext,
        request: SignupRequest,
        mail: MailProvider,
        *,
        log: Callable[[str], None],
    ) -> BrowserHandoff:
        # Materialize proxy giống browser phase nếu chưa có.
        req = request
        if not req.proxy and ctx.proxy_pool:
            proxy_url = ctx.proxy_pool.acquire_url()
            if proxy_url:
                req = request.model_copy(update={"proxy": proxy_url})

        # Mượn worker từ pool dùng chung thay vì spawn riêng mỗi job: đo được
        # 54 MB/Node process, chạy 200 luồng mà mỗi job một worker là 10.5 GB.
        from gpt_reg.sentinel.pool import get_pool

        pool = get_pool()
        worker = None
        try:
            try:
                worker = pool.acquire(log)
            except Exception as exc:
                log(f"[http] không mượn được sentinel worker, dùng one-shot: {exc}")
                worker = None

            max_state_attempts = 3
            for state_attempt in range(1, max_state_attempts + 1):
                try:
                    result = await asyncio.to_thread(_run_sync, ctx, req, mail, worker, log)
                    break
                except HttpRegError as exc:
                    if exc.step != "invalid_state" or state_attempt >= max_state_attempts:
                        raise
                    if ctx.should_cancel is not None and ctx.should_cancel():
                        raise JobCancelledError(step="http_rebootstrap") from exc
                    delay = 1.0 + random.uniform(0.2, 0.8)
                    log(
                        f"[http] auth state hết hạn ({state_attempt}/{max_state_attempts}) "
                        f"→ tạo session mới sau {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
        finally:
            pool.release(worker)

        return BrowserHandoff(
            cookies=result["cookies"],
            callback_url=result.get("callback_url"),
            otp_seconds=result.get("otp_seconds", 0.0),
            authenticated_email=result.get("authenticated_email"),
            access_token=result.get("access_token"),
            user_id=result.get("user_id"),
            account_password=result.get("account_password"),
            user_agent=result.get("user_agent"),
            impersonate=result.get("impersonate"),
            fingerprint_profile=result.get("fingerprint_profile"),
            registration_outcome=result.get("registration_outcome", "success"),
        )


async def run_http_reg_phase(
    ctx: RunContext,
    request: SignupRequest,
    mail: MailProvider,
    *,
    log: Callable[[str], None],
) -> BrowserHandoff:
    return await HttpRegPhase().run(ctx, request, mail, log=log)
