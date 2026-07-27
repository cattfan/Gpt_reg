from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from gpt_reg.browser.challenges import assert_not_blocked
from gpt_reg.browser.nextauth import bootstrap_authorize_url
from gpt_reg.core.exceptions import BrowserPhaseError
from gpt_reg.phases.browser import i18n
from gpt_reg.phases.browser import screens as scr
from gpt_reg.phases.browser import selectors as sel

# Gọi thẳng endpoint register trong page context — giữ nguyên cookie/Origin của
# SPA nên qua được Cloudflare, và trả về `continue_url` để đẩy flow sang bước
# gửi OTP. Port từ GSH `_REGISTER_USER_JS`.
_REGISTER_USER_JS = r"""
async ({username, password}) => {
    const res = await fetch('/api/accounts/user/register', {
        method: 'POST',
        credentials: 'include',
        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Origin': window.location.origin,
            'Referer': window.location.origin + '/create-account/password',
        },
        body: JSON.stringify({username, password}),
    });
    const text = await res.text();
    let body = null;
    try { body = JSON.parse(text); } catch { body = text; }
    return {status: res.status, body};
}
"""

_OTP_FORM_SELECTORS = (
    'input[name="code"]',
    'input[autocomplete="one-time-code"]',
    'input[inputmode="numeric"]',
)


async def _fill_first(page, selectors: tuple[str, ...], value: str) -> bool:
    for s in selectors:
        loc = page.locator(s).first
        try:
            if await loc.is_visible(timeout=1500):
                await loc.fill(value, timeout=5000)
                return True
        except Exception:
            continue
    return False


async def _click_first(page, selectors: tuple[str, ...]) -> bool:
    for s in selectors:
        loc = page.locator(s).first
        try:
            if await loc.is_visible(timeout=1500):
                await loc.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


async def goto_chatgpt(page, *, artifact_dir: Path, log) -> None:
    log("[browser] goto chatgpt.com")
    await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60_000)
    await assert_not_blocked(page, artifact_dir=artifact_dir, log=log)


async def bootstrap(
    page,
    *,
    email: str,
    device_id: str,
    logging_id: str,
    artifact_dir: Path,
    log,
) -> str:
    log("[browser] nextauth bootstrap")
    url = await bootstrap_authorize_url(
        page,
        device_id=device_id,
        email=email,
        logging_id=logging_id,
    )
    await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
    await assert_not_blocked(page, artifact_dir=artifact_dir, log=log)
    return url


async def enter_email_if_needed(page, email: str, log) -> None:
    if await _fill_first(page, sel.EMAIL_INPUT, email):
        log("[browser] filled email")
        await _click_first(page, sel.SUBMIT)


async def click_password_button(page, log) -> bool:
    """Chọn "Continue with password" trên màn hình `continue`."""
    try:
        btn = page.locator(scr.PASSWORD_BUTTON).first
        text = await btn.text_content(timeout=1000)
        await btn.click(timeout=3000)
        log(f"[browser] clicked password button: {(text or '').strip()[:60]}")
        return True
    except Exception as exc:
        log(f"[browser] click password button failed: {exc}")
        return False


async def register_user(page, *, email: str, password: str, log) -> dict[str, Any]:
    """POST /api/accounts/user/register trong page context.

    Return dict `{status, body}`; caller đọc `continue_url` trong body.
    """
    log(f"[browser] POST /api/accounts/user/register (email={email})")
    result = await page.evaluate(_REGISTER_USER_JS, {"username": email, "password": password})
    if not isinstance(result, dict):
        raise BrowserPhaseError(f"register unexpected result: {result}", step="register")
    return result


async def set_password(page, password: str, log) -> None:
    if await _fill_first(page, sel.PASSWORD_INPUT, password):
        log("[browser] filled password")
        await _click_first(page, sel.SUBMIT)


async def wait_otp_form(page, *, timeout_s: float, log) -> str:
    """Đợi OTP input hiện ra, return selector khớp."""
    for s in _OTP_FORM_SELECTORS:
        try:
            await page.wait_for_selector(s, state="visible", timeout=int(timeout_s * 1000))
            log(f"[browser] OTP input ready ({s})")
            return s
        except Exception:
            continue
    raise BrowserPhaseError(f"OTP input không xuất hiện sau {timeout_s}s", step="otp_wait")


async def submit_otp(page, code: str, log, *, selector: str | None = None) -> None:
    targets = (selector,) if selector else sel.OTP_INPUT
    if await _fill_first(page, tuple(t for t in targets if t), code):
        log("[browser] filled OTP")
        await _click_first(page, sel.SUBMIT)


async def submit_email(page, email: str, log) -> bool:
    """Màn /log-in-or-create-account: điền email rồi bấm tiếp tục."""
    if not await _fill_first(page, sel.EMAIL_INPUT, email):
        log("[browser] WARN không tìm thấy ô email")
        return False
    log(f"[browser] filled email {email}")
    if not await _click_first(page, sel.SUBMIT):
        # Không có nút nào khớp — Enter luôn hoạt động trên form 1 field.
        try:
            await page.locator(sel.EMAIL_INPUT[0]).first.press("Enter", timeout=3000)
            log("[browser] submitted email bằng Enter")
        except Exception as exc:
            log(f"[browser] WARN submit email thất bại: {exc}")
            return False
    return True


async def dismiss_optional_prompts(page, log: Callable[[str], None]) -> None:
    try:
        btn = page.locator(i18n.SKIP_BUTTON).first
        if await btn.is_visible(timeout=300):
            await btn.click(timeout=3000)
            log("[browser] dismissed optional prompt")
    except Exception:
        pass
