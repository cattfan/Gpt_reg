"""Bỏ qua màn hình đăng ký passkey. Port từ GSH `_skip_passkey`."""

from __future__ import annotations

from typing import Callable

from gpt_reg.phases.browser import i18n

_SKIP_SELECTORS = (
    i18n.SKIP_BUTTON,
    '[data-testid*="skip" i]',
    '[data-testid*="dismiss" i]',
)
_ENROLL_WORDS = i18n.ENROLL_WORDS


async def skip_passkey(page, *, log: Callable[[str], None], leave_timeout: float = 10.0) -> bool:
    """Click nút bỏ qua rồi đợi URL rời khỏi trang passkey.

    KHÔNG fallback `goto chatgpt.com` — sẽ cướp navigation OAuth callback đang
    inflight và làm mất Set-Cookie session-token (bug đã gặp ở GSH).
    """

    async def _left() -> bool:
        try:
            await page.wait_for_url(
                lambda u: "passkey" not in (u or "").lower(),
                timeout=int(leave_timeout * 1000),
            )
            return True
        except Exception:
            return False

    for sel in _SKIP_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=800):
                await btn.click(timeout=3000)
                log(f"[browser] passkey: clicked {sel}")
                if await _left():
                    return True
                # Page đã transition, đừng click selector khác.
                break
        except Exception:
            continue

    # Fallback: bất kỳ nút nào không phải nút tạo passkey.
    try:
        buttons = page.locator('button, a[role="button"]')
        for i in range(await buttons.count()):
            btn = buttons.nth(i)
            text = ((await btn.text_content()) or "").strip().lower()
            if not text or any(w in text for w in _ENROLL_WORDS):
                continue
            if await btn.is_visible(timeout=500):
                await btn.click(timeout=3000)
                log(f"[browser] passkey: clicked non-primary {text[:40]!r}")
                return await _left()
    except Exception:
        pass

    log("[browser] passkey: không rời được trang sau khi click")
    return False
