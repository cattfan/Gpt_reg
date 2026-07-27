"""Phase 2: session cookies from browser handoff → access_token via /api/auth/session."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from curl_cffi import requests as curl_requests

from gpt_reg.fingerprint import get_profile
from gpt_reg.models import BrowserHandoff, SignupRequest


class HttpPhaseError(Exception):
    """Phase 2 failed."""


_CHATGPT_BASE = "https://chatgpt.com"
_HTTP_RETRY_MAX = 3
_HTTP_RETRY_BACKOFF = (1.0, 2.0, 4.0)
_HTTP_RETRY_STATUS = frozenset({502, 503, 504, 408, 429})


def _request_with_retry(
    send: Callable[[], Any],
    *,
    log,
    label: str,
    max_attempts: int = _HTTP_RETRY_MAX,
) -> Any:
    last_exc: Exception | None = None
    last_response: Any = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = send()
            if response.status_code in _HTTP_RETRY_STATUS:
                last_response = response
                log(f"[http] {label} HTTP {response.status_code} attempt {attempt}/{max_attempts} — retry")
            else:
                return response
        except Exception as exc:
            last_exc = exc
            log(f"[http] {label} attempt {attempt}/{max_attempts} error: {type(exc).__name__}: {exc}")

        if attempt < max_attempts:
            backoff = _HTTP_RETRY_BACKOFF[min(attempt - 1, len(_HTTP_RETRY_BACKOFF) - 1)]
            time.sleep(backoff)

    if last_response is not None:
        return last_response
    raise HttpPhaseError(f"{label} failed sau {max_attempts} attempts: {last_exc}") from last_exc


def _build_session(*, request: SignupRequest) -> curl_requests.Session:
    if not request.fingerprint_profile:
        raise HttpPhaseError("request is missing fingerprint_profile")
    profile = get_profile(request.fingerprint_profile)
    if request.user_agent != profile.user_agent or request.impersonate != profile.impersonate:
        raise HttpPhaseError("request fingerprint fields do not match canonical profile")
    session = curl_requests.Session(impersonate=profile.impersonate)
    if request.proxy:
        session.proxies = {"http": request.proxy, "https": request.proxy}
    return session


def _fetch_access_token(
    *,
    session: curl_requests.Session,
    request: SignupRequest,
    log,
) -> tuple[str | None, str | None, str | None]:
    url = f"{_CHATGPT_BASE}/api/auth/session"
    headers = {
        "Accept": "application/json",
        "Referer": f"{_CHATGPT_BASE}/",
    }
    try:
        response = _request_with_retry(
            lambda: session.get(url, headers=headers, timeout=30),
            log=log,
            label="fetch-access-token",
        )
        if response.status_code != 200:
            log(f"[http] WARN /api/auth/session HTTP {response.status_code}")
            return None, None, None
        data = response.json()
        access = data.get("accessToken")
        user = data.get("user", {}) or {}
        authenticated_email = str(user.get("email") or "").strip() or None
        return access, user.get("id"), authenticated_email
    except Exception as exc:
        log(f"[http] WARN fetch access_token failed: {exc}")
        return None, None, None


def _cookies_for_backend(handoff: BrowserHandoff) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in handoff.cookies:
        domain = (c.get("domain") or "").lower()
        if "chatgpt.com" not in domain and "openai.com" not in domain:
            continue
        out.append(
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain"),
                "path": c.get("path"),
                "secure": c.get("secure", False),
            }
        )
    return out


def _extract_session_from_handoff(handoff: BrowserHandoff) -> dict[str, Any]:
    inject_cookies = _cookies_for_backend(handoff)
    out_cookies: list[dict[str, Any]] = []
    session_token: str | None = None
    session_token_chunks: dict[str, str] = {}
    account_id: str | None = None
    for c in handoff.cookies:
        domain = (c.get("domain") or "").lower()
        if "chatgpt.com" not in domain:
            continue
        out_cookies.append(
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain"),
                "path": c.get("path"),
                "secure": c.get("secure", False),
            }
        )
        name = c["name"]
        if name == "__Secure-next-auth.session-token":
            session_token = c["value"]
        elif name.startswith("__Secure-next-auth.session-token."):
            idx = name.rsplit(".", 1)[-1]
            session_token_chunks[idx] = c["value"]
        elif name == "_account":
            account_id = c["value"]

    if session_token is None and session_token_chunks:
        ordered = "".join(session_token_chunks[k] for k in sorted(session_token_chunks))
        session_token = ordered

    if not session_token:
        raise HttpPhaseError("handoff cookies không có __Secure-next-auth.session-token")
    return {
        "cookies": out_cookies,
        "inject_cookies": inject_cookies,
        "session_token": session_token,
        "account_id": account_id,
    }


async def run_http_phase(
    *,
    request: SignupRequest,
    handoff: BrowserHandoff,
    log,
) -> dict[str, Any]:
    def _sync() -> dict[str, Any]:
        result = _extract_session_from_handoff(handoff)
        log(f"[http] session-token from handoff ({len(result['session_token'])} bytes)")
        session = _build_session(request=request)
        try:
            for c in result.get("inject_cookies") or result["cookies"]:
                session.cookies.set(
                    c["name"],
                    c["value"],
                    domain=c.get("domain") or "chatgpt.com",
                    path=c.get("path") or "/",
                )
            access_token = handoff.access_token
            user_id = handoff.user_id
            authenticated_email = handoff.authenticated_email
            if access_token:
                log("[http] access token from browser handoff")
            else:
                fetched_access, fetched_user_id, fetched_email = None, None, None
                for attempt in range(1, 4):
                    fetched_access, fetched_user_id, fetched_email = _fetch_access_token(
                        session=session,
                        request=request,
                        log=log,
                    )
                    if fetched_access:
                        break
                    if attempt < 3:
                        log(f"[http] access_token retry {attempt}/3")
                        time.sleep(1.5 * attempt)
                access_token = access_token or fetched_access
                user_id = user_id or fetched_user_id
                authenticated_email = authenticated_email or fetched_email
            return {
                **result,
                "access_token": access_token,
                "user_id": user_id,
                "authenticated_email": authenticated_email,
            }
        finally:
            try:
                session.close()
            except Exception:
                pass

    return await asyncio.to_thread(_sync)
