"""Đăng nhập HTTP một account rồi đọc plan.

Dùng lại nguyên bộ helper của flow đăng ký (`gpt_reg.phases.http_reg`): bootstrap
xoay vân tay, `password/verify`, vượt `mfa_challenge` bằng TOTP, theo redirect,
lấy `/api/auth/session`. Khác duy nhất: KHÔNG đăng ký, và bước cuối gọi
`/backend-api/accounts/check` để lấy plan.

Trả về dict phẳng cho `check_manager` ghi thẳng vào bảng `checks`.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable

from gpt_reg.checker.combo import CheckCombo
from gpt_reg.core.exceptions import JobCancelledError
from gpt_reg.fingerprint import profile_for_seed
from gpt_reg.phases import http_reg as hr

_ACCOUNTS_CHECK_URL = "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
_ME_URL = "https://chatgpt.com/backend-api/me"

# plan_type thô của server → nhãn hiển thị. Giữ nguyên key lạ (fallback) để không
# giấu mất plan mới mà OpenAI thêm sau này.
_PLAN_LABELS = {
    "free": "Free",
    "plus": "Plus",
    "pro": "Pro",
    "team": "Team",
    "business": "Business",
    "enterprise": "Enterprise",
    "edu": "Edu",
}


class CheckError(Exception):
    """Không đọc được plan. `kind` để phân loại trạng thái ở tầng trên.

    kind:
      - "die"          : login bị từ chối (sai pass/2fa, account chết/khoá).
      - "need_2fa"     : account bật 2FA nhưng combo không có secret.
      - "need_fullmail": login đòi verify email nhưng combo không kèm fullmail.
      - "not_found"    : account không tồn tại (server đưa tới màn đăng ký).
      - "error"        : tạm thời / không xác định (mạng, CF, thiếu token).
    """

    def __init__(self, message: str, *, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


def _backend_headers(session, access_token: str) -> dict[str, str]:
    headers = hr._common_headers(session, "https://chatgpt.com/")
    headers["Authorization"] = f"Bearer {access_token}"
    # /backend-api từ origin chatgpt.com: thiếu Sec-Fetch-* thì WAF coi là bot.
    headers["sec-fetch-dest"] = "empty"
    headers["sec-fetch-mode"] = "cors"
    headers["sec-fetch-site"] = "same-origin"
    return headers


def _fetch_plan(session, access_token: str, log: Callable) -> dict[str, Any]:
    r = session.get(_ACCOUNTS_CHECK_URL, headers=_backend_headers(session, access_token), timeout=30)
    if r.status_code != 200:
        raise CheckError(f"accounts/check HTTP {r.status_code}", kind="error")
    data = r.json() or {}
    accounts = data.get("accounts") or {}
    # Ưu tiên account "default"; nếu không có thì lấy cái đầu tiên.
    acc = accounts.get("default")
    if acc is None and accounts:
        acc = next(iter(accounts.values()))
    acc = acc or {}
    account = acc.get("account") or {}
    ent = acc.get("entitlement") or {}
    plan_type = str(account.get("plan_type") or "").strip().lower()
    return {
        "plan": _PLAN_LABELS.get(plan_type, plan_type or "?"),
        "plan_raw": plan_type,
        "plan_detail": str(ent.get("subscription_plan") or "").strip(),
        "has_subscription": bool(ent.get("has_active_subscription")),
        "expires_at": ent.get("expires_at"),
        "deactivated": bool(account.get("is_deactivated")),
    }


def _fetch_me(session, access_token: str, log: Callable) -> dict[str, Any]:
    # Không bắt buộc — chỉ để biết email xác thực + cờ 2FA. Lỗi thì bỏ qua.
    try:
        r = session.get(_ME_URL, headers=_backend_headers(session, access_token), timeout=20)
        if r.status_code != 200:
            return {}
        d = r.json() or {}
        return {
            "authenticated_email": str(d.get("email") or "").strip(),
            "mfa_enabled": bool(d.get("mfa_flag_enabled")),
        }
    except Exception:
        return {}


def _login_via_email_otp(session, device_id, combo: CheckCombo, proxy, log, should_cancel) -> str:
    """Account đòi verify email khi đăng nhập → đọc mã qua Graph. Cần fullmail."""
    if not combo.has_full_combo:
        raise CheckError(
            "login đòi verify email — thêm |fullmail vào combo để đọc mã OTP",
            kind="need_fullmail",
        )
    from gpt_reg.config import load_settings
    from gpt_reg.mail.providers import build_provider

    settings = load_settings()
    mail = build_provider(
        "outlook", combo_line=combo.full_combo,
        state_dir=settings.outlook_state_dir, proxy_url=proxy,
    )
    since = datetime.now(timezone.utc)
    hr._step_send_otp(session, device_id, log)
    req = SimpleNamespace(
        email=combo.email, otp_timeout_seconds=120.0, otp_poll_interval_seconds=5.0
    )
    code, _ = hr._poll_otp(mail, req, since, set(), should_cancel, log)
    resp = hr._step_verify_otp(session, code, device_id, log)
    if not resp.get("_ok"):
        raise CheckError(f"verify OTP fail HTTP {resp.get('_status')}", kind="die")
    return (resp.get("continue_url") or "").strip()


def check_account(
    combo: CheckCombo, proxy: str | None, log: Callable[[str], None],
    *, worker=None, should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Đăng nhập rồi đọc plan. Trả dict {ok, plan, ...} hoặc ném CheckError."""
    fingerprint_seed = hashlib.sha256(
        f"gpt-reg:checker:v1:{combo.email.strip().lower()}".encode("utf-8")
    ).hexdigest()[:32]
    fingerprint_profile = profile_for_seed(fingerprint_seed).name
    session, device_id, landing, auth_url = hr._bootstrap_with_profile_rotation(
        proxy,
        log,
        fingerprint_seed=fingerprint_seed,
        preferred_profile=fingerprint_profile,
        login_hint=combo.email,
    )
    try:
        kind = hr.classify_landing(landing)
        log(f"[check] landing={kind} ({landing.split('?')[0][:60]})")
        landed_otp = kind == "otp"  # account passwordless/onboarding

        if kind == "register":
            # login_hint đưa tới màn ĐĂNG KÝ nghĩa là email chưa có account.
            raise CheckError("account không tồn tại", kind="not_found")

        if kind == "login":
            try:
                login = hr._step_login_password(session, combo.password, device_id, log, worker=worker)
            except hr.HttpRegError as exc:
                if getattr(exc, "step", "") == "wrong_password":
                    raise CheckError("sai mật khẩu", kind="die") from exc
                raise CheckError(f"login lỗi: {exc}", kind="error") from exc
            page_type = login.get("page_type") or ""
            cont = login.get("continue_url") or ""
            if page_type == "mfa_challenge" or "/mfa-challenge" in cont:
                if not combo.has_totp:
                    raise CheckError("account bật 2FA nhưng combo không có secret", kind="need_2fa")
                try:
                    cont = hr._step_mfa_challenge(session, combo.totp_secret, cont, device_id, log) or cont
                except hr.HttpRegError as exc:
                    raise CheckError(f"2FA sai/không vượt được: {exc}", kind="die") from exc
            elif page_type in ("email_otp_verification", "email_verification") or "/email-verification" in cont:
                cont = _login_via_email_otp(session, device_id, combo, proxy, log, should_cancel) or cont
        elif kind == "otp":
            # Account chưa verify email (hoặc đăng ký nửa chừng) → verify rồi vào.
            cont = _login_via_email_otp(session, device_id, combo, proxy, log, should_cancel)
        else:
            raise CheckError(f"trạng thái lạ (landing={kind})", kind="error")

        # continue_url → callback → session token.
        callback = hr._step_follow_redirects(session, cont or auth_url, log, should_cancel=should_cancel)
        if callback:
            hr._consume_callback(session, callback, log)
        _st, access_token, _uid, email = hr._get_session_tokens(session, log)
        if not access_token and auth_url:
            # continue_url không ra code → chạy lại authorize cho phiên đã xác thực.
            callback = hr._step_follow_redirects(session, auth_url, log, should_cancel=should_cancel)
            if callback:
                hr._consume_callback(session, callback, log)
            _st, access_token, _uid, email = hr._get_session_tokens(session, log)
        if not access_token:
            if landed_otp:
                # Account passwordless kẹt onboarding: verify email xong vẫn quay
                # về /email-verification, chặng cuối là trang SPA mà HTTP thuần
                # không chạy được. Không phải lỗi tạm thời — báo rõ để khỏi retry.
                raise CheckError(
                    "account chưa hoàn tất onboarding (passwordless) — không đọc được plan qua HTTP",
                    kind="onboarding",
                )
            raise CheckError("đăng nhập xong nhưng không lấy được access_token", kind="error")

        plan = _fetch_plan(session, access_token, log)
        me = _fetch_me(session, access_token, log)
        if plan.get("deactivated"):
            log("[check] account bị vô hiệu hoá (deactivated)")
        log(f"[check] plan={plan['plan']} sub={plan['has_subscription']} 2fa={me.get('mfa_enabled')}")
        return {
            "ok": True,
            "email": me.get("authenticated_email") or email or combo.email,
            **plan,
            "mfa_enabled": bool(me.get("mfa_enabled") or combo.has_totp),
        }
    except JobCancelledError:
        raise
    except CheckError:
        raise
    except Exception as exc:  # mạng/CF/parse — tạm thời, cho retry
        raise CheckError(f"{type(exc).__name__}: {exc}", kind="error") from exc
    finally:
        try:
            session.close()
        except Exception:
            pass
