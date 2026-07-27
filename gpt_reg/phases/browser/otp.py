"""Poll OTP từ mail + submit, kèm leo thang khi form không tự chuyển màn.

Bản cũ submit xong là chờ thụ động — đúng triệu chứng của DidatoBascetta11 và
HenniganSharpless849: OTP điền được, rồi đứng im tới hết deadline 300s. GSH
`_wait_after_otp` leo thang nhiều nấc trước khi bỏ cuộc; port lại ở đây.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Callable

from gpt_reg.core.contracts import MailProvider
from gpt_reg.core.exceptions import JobCancelledError
from gpt_reg.phases.browser import i18n
from gpt_reg.phases.browser import register as reg
from gpt_reg.phases.browser import selectors as sel

# Submit OTP không qua UI. Chạy trong page context để giữ cookie + Origin.
_VALIDATE_OTP_JS = r"""
async ({code}) => {
    const res = await fetch('/api/accounts/email-otp/validate', {
        method: 'POST',
        credentials: 'include',
        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Origin': 'https://auth.openai.com',
            'Referer': 'https://auth.openai.com/email-verification',
        },
        body: JSON.stringify({code}),
    });
    const text = await res.text();
    let body = null;
    try { body = JSON.parse(text); } catch { body = text; }
    return {status: res.status, body};
}
"""

RECLICK_AFTER = 10.0
JS_SUBMIT_AFTER = 18.0
API_AFTER = 25.0
REPOLL_AFTER = 35.0
POLL_SLICE_S = 12.0

# Lỗi mạng tạm thời khi gọi Graph qua proxy — gặp thật:
# "ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING]" làm chết cả job.
_TRANSIENT_MAIL_ERRORS = (
    "ssl",
    "eof",
    "connecterror",
    "connectionerror",
    "connection reset",
    "timed out",
    "timeout",
    "temporarily",
    "502",
    "503",
    "504",
)


def _is_transient(exc: BaseException) -> bool:
    blob = f"{type(exc).__name__}: {exc}".casefold()
    return any(marker in blob for marker in _TRANSIENT_MAIL_ERRORS)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def poll_code(
    mail: MailProvider,
    *,
    email: str,
    since: datetime,
    timeout_s: float,
    poll_interval_s: float,
    log: Callable[[str], None],
    consumed: set[str],
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[str, float]:
    """Lấy 1 mã OTP chưa dùng. Return (code, seconds_waited)."""
    started = time.monotonic()
    loop = asyncio.get_event_loop()
    deadline = started + timeout_s
    transient_failures = 0
    while time.monotonic() < deadline:
        if should_cancel is not None and should_cancel():
            raise JobCancelledError(step="otp_poll")
        # Chờ theo lát ngắn thay vì một lần 180s: giữ cho Stop phản hồi nhanh và
        # cho lỗi mạng tạm thời cơ hội retry sớm.
        remaining = deadline - time.monotonic()
        slice_s = max(2.0, min(POLL_SLICE_S, remaining))
        try:
            code = await loop.run_in_executor(
                None,
                lambda s=slice_s: mail.wait_for_otp(
                    email=email,
                    since=since,
                    timeout_s=s,
                    poll_interval_s=poll_interval_s,
                    log=log,
                ),
            )
        except TimeoutError:
            continue  # chưa có mail trong lát này — vòng sau kiểm tra cancel
        except Exception as exc:
            if not _is_transient(exc) or transient_failures >= 3:
                raise
            transient_failures += 1
            backoff = 2.0 * transient_failures
            log(
                f"[browser] mail lỗi tạm thời ({type(exc).__name__}) — "
                f"retry {transient_failures}/3 sau {backoff:.0f}s"
            )
            await asyncio.sleep(backoff)
            continue
        if code not in consumed:
            consumed.add(code)
            return code, time.monotonic() - started
        log(f"[browser] OTP {code[:2]}**** đã dùng — đợi mã mới")
        await asyncio.sleep(poll_interval_s)
    raise TimeoutError(f"không nhận được mã OTP mới sau {timeout_s}s")


async def validate_via_api(page, code: str, log: Callable[[str], None]) -> str | None:
    """Fallback: gọi thẳng endpoint validate. Return `continue_url` nếu có."""
    try:
        result = await page.evaluate(_VALIDATE_OTP_JS, {"code": code})
    except Exception as exc:
        log(f"[browser] OTP API fallback lỗi: {type(exc).__name__}: {exc}")
        return None
    status = (result or {}).get("status")
    body = (result or {}).get("body") or {}
    log(f"[browser] OTP API fallback → HTTP {status}")
    if not isinstance(status, int) or status >= 400:
        return None
    if isinstance(body, dict):
        cont = body.get("continue_url")
        if isinstance(cont, str) and cont.strip():
            return cont.strip()
    return None


async def advance_after_api(page, continue_url: str | None, log: Callable[[str], None]) -> None:
    """Đẩy page sang bước kế sau khi validate bằng API."""
    if continue_url:
        target = continue_url
        if target.startswith("/"):
            target = f"https://auth.openai.com{target}"
        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
            log("[browser] mở continue_url sau OTP")
        except Exception as exc:
            log(f"[browser] mở continue_url lỗi: {type(exc).__name__}: {exc}")
        return
    # Một số response commit verification vào cookie nhưng không trả URL —
    # reload để server đưa ra state kế tiếp thay vì đứng ở document OTP cũ.
    try:
        await page.reload(wait_until="domcontentloaded", timeout=30_000)
        log("[browser] reload sau khi validate OTP qua API")
    except Exception as exc:
        log(f"[browser] reload sau OTP lỗi: {type(exc).__name__}: {exc}")


class OtpSubmission:
    """Trạng thái 1 lần submit OTP + leo thang khi màn hình không đổi."""

    def __init__(self, code: str, log: Callable[[str], None]) -> None:
        self.code = code
        self.log = log
        self.submitted_at = time.monotonic()
        self._reclick = False
        self._js = False
        self._api = False

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.submitted_at

    async def escalate(self, page) -> bool:
        """Chạy nấc leo thang kế tiếp. Return True khi cần poll mã mới."""
        elapsed = self.elapsed
        if elapsed > REPOLL_AFTER:
            # Phải BẤM RESEND trước khi đi chờ mã mới. Trước đây chỉ xoá ô nhập
            # rồi poll tiếp — server chưa gửi mail nào nữa nên vòng poll đứng đủ
            # `otp_timeout_seconds` (mặc định 180s) chờ thứ không bao giờ đến,
            # vượt luôn deadline 300s của cả flow.
            self.log(f"[browser] OTP kẹt >{REPOLL_AFTER:.0f}s — resend rồi chờ mã mới")
            await click_resend(page, self.log)
            try:
                await page.locator('input[name="code"]').first.fill("")
            except Exception:
                pass
            return True
        if elapsed > API_AFTER and not self._api:
            self._api = True
            self.log("[browser] OTP: UI+JS không ăn — validate qua API")
            cont = await validate_via_api(page, self.code, self.log)
            await advance_after_api(page, cont, self.log)
        elif elapsed > JS_SUBMIT_AFTER and not self._js:
            self._js = True
            self.log("[browser] OTP: click không ăn — thử form.submit() qua JS")
            try:
                await page.evaluate(
                    "() => { const f = document.querySelector('form'); if (f) f.submit(); }"
                )
            except Exception as exc:
                self.log(f"[browser] JS form.submit() lỗi: {type(exc).__name__}: {exc}")
        elif elapsed > RECLICK_AFTER and not self._reclick:
            self._reclick = True
            self.log(f"[browser] OTP vẫn ở màn cũ sau {elapsed:.0f}s — click submit lại")
            for btn in sel.SUBMIT:
                try:
                    await page.click(btn, timeout=2000)
                    break
                except Exception:
                    continue
        return False


async def detect_rejection(page) -> str | None:
    """Trả về text lỗi nếu OTP bị từ chối."""
    try:
        el = page.locator('[role="alert"], [class*="error"]').first
        text = await el.text_content(timeout=200)
    except Exception:
        return None
    if not text:
        return None
    if i18n.contains_any(text, i18n.REJECTED):
        return text.strip()
    return None


async def click_resend(page, log: Callable[[str], None]) -> None:
    try:
        btn = page.locator(i18n.RESEND_BUTTON).first
        await btn.click(timeout=3000)
        log("[browser] clicked Resend")
    except Exception as exc:
        log(f"[browser] không thấy nút Resend: {exc}")


async def submit(
    page, code: str, log: Callable[[str], None], *, selector: str | None = None
) -> None:
    await reg.submit_otp(page, code, log, selector=selector)
