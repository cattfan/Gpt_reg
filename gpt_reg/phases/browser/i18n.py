"""Text cho selector — UI OpenAI đổi ngôn ngữ theo locale/geoip của proxy.

Proxy VN + `browser_locale=vi-VN` làm ChatGPT render tiếng Việt, nên mọi selector
`has-text("Continue")` đều trượt. Ưu tiên selector theo thuộc tính
(`button[type="submit"]`, `input[type="email"]`) vì chúng độc lập ngôn ngữ; chỉ
dùng text khi không còn cách nào khác, và khi đó phải liệt kê đủ hai ngôn ngữ.

`has-text` của Playwright là so khớp substring, không phân biệt hoa thường.
"""

from __future__ import annotations

PASSWORD = ("password", "mật khẩu")
CONTINUE = ("Continue", "Tiếp tục")
VERIFY = ("Verify", "Xác minh", "Xác nhận")
SUBMIT_EXTRA = ("Next", "Tiếp theo", "Submit", "Gửi", "Agree", "Đồng ý")
SKIP = (
    "Skip",
    "Bỏ qua",
    "Để sau",
    "Lúc khác",
    "Not now",
    "Maybe later",
    "Do this later",
)
RESEND = ("Resend", "Gửi lại")
ACCEPT = ("Okay", "OK", "I agree", "Tôi đồng ý", "Accept", "Chấp nhận", "Got it", "Đã hiểu")
TRY_AGAIN = ("Try again", "Thử lại", "Retry")
INBOX = (
    "Check your inbox",
    "Check your email",
    "Enter the verification code",
    "Kiểm tra hộp thư",
    "Kiểm tra email",
    "Nhập mã xác minh",
)
AUTHENTICATOR = (
    "authenticator app",
    "two-factor",
    "two factor",
    "authenticator code",
    "google authenticator",
    "ứng dụng xác thực",
    "xác thực hai yếu tố",
)
REJECTED = (
    "incorrect",
    "wrong",
    "invalid",
    "expired",
    "không đúng",
    "không hợp lệ",
    "hết hạn",
    "sai mã",
)
ENROLL_WORDS = ("create", "set up", "enable", "passkey", "tạo", "thiết lập", "bật")
NOT_SUBMIT_WORDS = (
    "cancel",
    "back",
    "sign out",
    "log out",
    "hủy",
    "quay lại",
    "đăng xuất",
)


def any_text(tags: tuple[str, ...], phrases: tuple[str, ...]) -> str:
    """CSS selector khớp bất kỳ tag nào chứa bất kỳ cụm text nào."""
    return ", ".join(f'{tag}:has-text("{p}")' for tag in tags for p in phrases)


def text_markers(phrases: tuple[str, ...]) -> str:
    return ", ".join(f'text="{p}"' for p in phrases)


def contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    low = (text or "").casefold()
    return any(p.casefold() in low for p in phrases)


BUTTONS = ("button", "a", '[role="button"]')
PASSWORD_BUTTON = any_text(BUTTONS, PASSWORD)
SKIP_BUTTON = any_text(("button", "a"), SKIP)
RESEND_BUTTON = any_text(("button", "a"), RESEND)
ACCEPT_BUTTON = any_text(("button",), ACCEPT)
TRY_AGAIN_BUTTON = any_text(("button", "a"), TRY_AGAIN)
AUTHENTICATOR_TEXT = ", ".join(f"text=/{p}/i" for p in AUTHENTICATOR)
INBOX_MARKERS = text_markers(INBOX)
SUBMIT_BUTTONS = ('button[type="submit"]',) + tuple(
    f'button:has-text("{p}")' for p in CONTINUE + VERIFY + SUBMIT_EXTRA
)
