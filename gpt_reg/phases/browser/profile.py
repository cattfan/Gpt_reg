from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from gpt_reg.core.exceptions import BrowserPhaseError

_SESSION_COOKIES = (
    "__Secure-next-auth.session-token",
    "__Secure-next-auth.session-token.0",
)


def has_session_cookie(cookies: list[dict[str, Any]]) -> bool:
    for c in cookies:
        name = str(c.get("name") or "")
        if name in _SESSION_COOKIES or name.startswith("__Secure-next-auth.session-token."):
            return True
    return False


async def wait_session_cookie(
    ctx,
    page,
    *,
    timeout_s: float = 60.0,
    log: Callable[[str], None],
    force_goto_after: float = 8.0,
) -> list[dict[str, Any]]:
    """Đợi `__Secure-next-auth.session-token` xuất hiện.

    Callback OAuth chạy bằng fetch nền nên page có thể vẫn ở auth.openai.com khi
    cookie đã set. Nếu sau `force_goto_after` giây vẫn chưa thấy, chủ động
    navigate top-level tới chatgpt.com để server commit cookie — chỉ làm 1 lần.
    Port từ GSH `_wait_chatgpt_session`.
    """
    deadline = time.monotonic() + timeout_s
    goto_at = time.monotonic() + force_goto_after
    goto_done = False
    while time.monotonic() < deadline:
        cookies = await ctx.cookies()
        if has_session_cookie(cookies):
            names = {str(c.get("name") or "") for c in cookies}
            log(
                f"[browser] session cookie present "
                f"({len(cookies)} cookies, _account={'_account' in names})"
            )
            await asyncio.sleep(0.3)
            return cookies

        if not goto_done and time.monotonic() > goto_at:
            goto_done = True
            cur = page.url or ""
            if "chatgpt.com" in cur and "auth.openai.com" not in cur:
                log("[browser] chưa có session-token sau 8s nhưng đang ở chatgpt.com — poll tiếp")
            else:
                log(f"[browser] chưa có session-token sau 8s (url={cur.split('?')[0]}) — force goto")
                try:
                    await page.goto(
                        "https://chatgpt.com/", wait_until="domcontentloaded", timeout=20_000
                    )
                except Exception as exc:
                    log(f"[browser] force goto failed: {type(exc).__name__}: {exc}")
        await asyncio.sleep(0.35)
    raise BrowserPhaseError(f"session cookie timeout sau {timeout_s}s", step="session_wait")
