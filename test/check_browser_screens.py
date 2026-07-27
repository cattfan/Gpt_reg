"""Kiểm tra thứ tự ưu tiên của `screens.detect_screen` bằng page giả.

Thứ tự phân loại là contract: MFA trước OTP (trùng selector `input[name=code]`),
nút password trước OTP form, Turnstile trước OTP (challenge overlay lên form).
"""

from __future__ import annotations

import asyncio

from gpt_reg.phases.browser import screens as scr

_TURNSTILE = (
    'iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], '
    "#cf-turnstile, .cf-turnstile, [data-turnstile-callback]"
)
_MFA_TEXT = scr.i18n.AUTHENTICATOR_TEXT
_INBOX = scr.i18n.INBOX_MARKERS
_PWD_BTN = scr.PASSWORD_BUTTON


class _Locator:
    def __init__(self, visible: bool) -> None:
        self._visible = visible

    @property
    def first(self) -> "_Locator":
        return self

    async def is_visible(self, timeout: int = 0) -> bool:
        return self._visible


class _Page:
    def __init__(self, url: str, visible: tuple[str, ...] = ()) -> None:
        self.url = url
        self._visible = set(visible)

    def locator(self, selector: str) -> _Locator:
        return _Locator(selector in self._visible)


CASES = (
    ("error page", "https://auth.openai.com/auth/error?x=1", (), scr.AUTH_ERROR),
    ("logged in", "https://chatgpt.com/", (), scr.CHATGPT),
    ("about-you url", "https://auth.openai.com/about-you", (), scr.ABOUT_YOU),
    ("about-you SPA", "https://auth.openai.com/email-verification", (scr.NAME_INPUT,), scr.ABOUT_YOU),
    ("passkey", "https://auth.openai.com/passkey-enroll", (), scr.PASSKEY_ENROLL),
    ("mfa beats otp", "https://auth.openai.com/log-in/password", (scr.OTP_INPUT, _MFA_TEXT), scr.MFA_CHALLENGE),
    ("password login not email", "https://auth.openai.com/log-in/password", (scr.EMAIL_INPUT,), scr.PASSWORD_LOGIN),
    ("password create", "https://auth.openai.com/create-account/password", (), scr.PASSWORD_CREATE),
    ("password login", "https://auth.openai.com/log-in/password", (), scr.PASSWORD_LOGIN),
    ("login to otp SPA", "https://auth.openai.com/log-in/password", (scr.OTP_INPUT,), scr.OTP),
    ("login to inbox SPA", "https://auth.openai.com/log-in/password", (_INBOX,), scr.OTP),
    ("reset password", "https://auth.openai.com/reset-password", (), scr.PASSWORD_CREATE),
    ("reset with otp", "https://auth.openai.com/reset-password", (scr.OTP_INPUT,), scr.OTP),
    # Màn đầu tiên: /log-in-or-create-account chỉ có ô email + nút "Tiếp tục".
    ("email entry", "https://auth.openai.com/log-in-or-create-account", (scr.EMAIL_INPUT,), scr.EMAIL_ENTRY),
    ("email entry bare", "https://auth.openai.com/log-in-or-create-account", (), scr.UNKNOWN),
    ("email entry w/ pwd btn", "https://auth.openai.com/log-in-or-create-account", (scr.EMAIL_INPUT, _PWD_BTN), scr.CONTINUE),
    ("email input fallback", "https://auth.openai.com/somewhere", (scr.EMAIL_INPUT,), scr.EMAIL_ENTRY),
    ("continue beats otp", "https://auth.openai.com/email-verification", (scr.OTP_INPUT, _PWD_BTN), scr.CONTINUE),
    ("otp only", "https://auth.openai.com/email-verification", (scr.OTP_INPUT,), scr.OTP),
    ("otp url fallback", "https://auth.openai.com/email-verification", (), scr.OTP),
    ("turnstile beats otp", "https://auth.openai.com/challenge", (scr.OTP_INPUT, _TURNSTILE), scr.TURNSTILE),
    ("unknown", "https://auth.openai.com/whatever", (), scr.UNKNOWN),
)


async def _run() -> int:
    failed = 0
    for label, url, visible, expected in CASES:
        got = await scr.detect_screen(_Page(url, visible))
        if got != expected:
            failed += 1
            print(f"[fail] {label}: {got} (want {expected})")
    if failed:
        print(f"[fail] browser screens {len(CASES) - failed}/{len(CASES)}")
    else:
        print(f"[ok] browser screens {len(CASES)}/{len(CASES)}")
    return failed


if __name__ == "__main__":
    raise SystemExit(1 if asyncio.run(_run()) else 0)
