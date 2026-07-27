from __future__ import annotations

from gpt_reg.core.constants import EXIT_CHALLENGE
from gpt_reg.core.exceptions import ChallengeBlockedError

# Turnstile của Cloudflare thường TỰ GIẢI sau vài giây trong Camoufox nên KHÔNG
# nằm ở đây. Trước kia nó bị gộp chung: `assert_not_blocked` chạy đầu mỗi vòng
# lặp và giết job ngay khi thấy iframe, biến nhánh `turnstile_challenge` trong
# drive loop (vốn để chờ tự giải, tối đa 60 vòng) thành code chết.
_HARD_CAPTCHA_SELECTORS = (
    'iframe[src*="hcaptcha"]',
    'iframe[src*="recaptcha"]',
    'iframe[src*="arkoselabs"]',
    "#funcaptcha",
)

# Turnstile — chặn MỀM: để drive loop chờ rồi mới bỏ cuộc.
_TURNSTILE_SELECTORS = (
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[src*="turnstile"]',
    "#cf-turnstile",
    ".cf-turnstile",
)

# Chỉ dùng cụm thật đặc trưng. "SMS" hay "Verify you are human" trước đây quá
# rộng: chúng xuất hiện cả trên trang Turnstile bình thường và trên màn OTP,
# gây fail nhầm.
_PHONE_MARKERS = (
    "enter your phone",
    "phone number",
    "nhập số điện thoại",
    "số điện thoại",
    "verify your phone",
)


async def _visible(page, selector: str) -> bool:
    try:
        return await page.locator(selector).first.is_visible(timeout=300)
    except Exception:
        return False


async def has_turnstile(page) -> bool:
    for sel in _TURNSTILE_SELECTORS:
        if await _visible(page, sel):
            return True
    return False


async def detect_block(page) -> str | None:
    """Loại chặn CỨNG, hoặc None. Turnstile không tính là chặn cứng."""
    for sel in _HARD_CAPTCHA_SELECTORS:
        if await _visible(page, sel):
            return "captcha"
    try:
        body = (await page.inner_text("body"))[:4000].lower()
    except Exception:
        return None
    if any(marker in body for marker in _PHONE_MARKERS):
        return "phone"
    return None


async def assert_not_blocked(page, *, artifact_dir, log) -> None:
    kind = await detect_block(page)
    if not kind:
        return
    from gpt_reg.browser import artifacts

    path = await artifacts.screenshot(page, artifact_dir, f"block_{kind}")
    log(f"[challenge] blocked ({kind}) url={page.url} screenshot={path}")
    raise ChallengeBlockedError(f"blocked: {kind}", kind=kind)


def challenge_exit_code(exc: ChallengeBlockedError) -> int:
    return EXIT_CHALLENGE
