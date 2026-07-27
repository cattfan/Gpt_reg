"""Phân loại màn hình auth của OpenAI.

Port từ `privateGSH/browser_phase.py::_detect_screen`. Drive loop dispatch theo
tên màn hình thay vì đoán từ URL substring — nguyên nhân flow submit OTP xong
rồi đứng im vì không có nhánh nào xử lý màn hình kế tiếp.

Thứ tự kiểm tra là một phần của contract, đừng sắp xếp lại:
  - MFA phải đứng trước OTP: cả hai dùng chung `input[name="code"]`.
  - Nút "continue with password" thắng OTP form: ta cần account có password.
  - Turnstile phải đứng trước OTP: challenge có thể overlay lên OTP form.
"""

from __future__ import annotations

from gpt_reg.phases.browser import i18n

CHATGPT = "chatgpt"
ABOUT_YOU = "about_you"
PASSKEY_ENROLL = "passkey_enroll"
MFA_CHALLENGE = "mfa_challenge"
TURNSTILE = "turnstile_challenge"
OTP = "otp"
PASSWORD_CREATE = "password_create"
PASSWORD_LOGIN = "password_login"
EMAIL_ENTRY = "email_entry"
CONTINUE = "continue"
AUTH_ERROR = "auth_error"
UNKNOWN = "unknown"

OTP_INPUT = 'input[name="code"], input[autocomplete="one-time-code"]'
NAME_INPUT = 'input[name="name"], input[autocomplete="name"]'
EMAIL_INPUT = 'input[type="email"], input[name="email"], input[autocomplete="email"]'
PASSWORD_INPUT = 'input[type="password"]'
PASSWORD_BUTTON = i18n.PASSWORD_BUTTON
_TURNSTILE = (
    'iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], '
    "#cf-turnstile, .cf-turnstile, [data-turnstile-callback]"
)
_EMAIL_ENTRY_PATHS = ("/log-in-or-create-account", "/log-in-or-sign-up", "/identifier")
_PASSWORD_CREATE_PATHS = (
    "/reset-password",
    "/password/reset",
    "/password/create",
    "/password/new",
)
_OTP_PATHS = ("/email-verification", "/email-otp")


async def visible(page, selector: str, *, timeout_ms: int = 200) -> bool:
    try:
        return await page.locator(selector).first.is_visible(timeout=timeout_ms)
    except Exception:
        return False


async def shows_authenticator_mfa(page) -> bool:
    """True khi challenge đang hiện là TOTP/authenticator, không phải email OTP."""
    cur = (page.url or "").casefold()
    if any(p in cur for p in ("/mfa-challenge", "/totp", "/two-factor")):
        if await visible(page, OTP_INPUT):
            return True
    return await visible(page, i18n.AUTHENTICATOR_TEXT)


async def detect_screen(page) -> str:
    cur = page.url or ""
    if "/auth/error" in cur:
        return AUTH_ERROR
    if "chatgpt.com" in cur and "auth.openai.com" not in cur:
        return CHATGPT
    if "auth.openai.com/about-you" in cur:
        return ABOUT_YOU
    if "passkey" in cur.lower():
        return PASSKEY_ENROLL
    # SPA có thể render /about-you trước khi URL đổi.
    if await visible(page, NAME_INPUT):
        return ABOUT_YOU

    if await shows_authenticator_mfa(page):
        return MFA_CHALLENGE

    if "/create-account/password" in cur:
        return PASSWORD_CREATE
    if any(p in cur for p in _PASSWORD_CREATE_PATHS):
        if await visible(page, OTP_INPUT):
            return OTP
        return PASSWORD_CREATE
    if "/log-in/password" in cur:
        # URL không đổi nhưng SPA đã chuyển sang OTP form / "Kiểm tra hộp thư".
        if await visible(page, OTP_INPUT):
            return OTP
        if await visible(page, i18n.INBOX_MARKERS):
            return OTP
        return PASSWORD_LOGIN

    # Màn nhập email đầu tiên (/log-in-or-create-account). Phải đứng trước nhánh
    # `continue`: trang này có nút "Tiếp tục" nhưng chưa có nút password nào.
    if any(p in cur for p in _EMAIL_ENTRY_PATHS):
        if await visible(page, PASSWORD_BUTTON, timeout_ms=500):
            return CONTINUE
        if await visible(page, EMAIL_INPUT, timeout_ms=500):
            return EMAIL_ENTRY

    if any(p in cur for p in _OTP_PATHS):
        if await visible(page, PASSWORD_BUTTON, timeout_ms=800):
            return CONTINUE
    if "auth.openai.com" in cur and await visible(page, PASSWORD_BUTTON, timeout_ms=300):
        return CONTINUE

    if await visible(page, _TURNSTILE):
        return TURNSTILE
    if await visible(page, OTP_INPUT):
        return OTP
    if any(p in cur for p in _OTP_PATHS):
        return OTP
    # Fallback cuối: chỉ có ô email, chưa có password/OTP → vẫn là màn nhập email.
    if await visible(page, EMAIL_INPUT) and not await visible(page, PASSWORD_INPUT):
        return EMAIL_ENTRY
    return UNKNOWN
