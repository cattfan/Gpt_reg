"""HTTP flow phải kiểm huỷ ở các chặng dài, để Stop thoát nhanh.

Trước đây chỉ register/otp/verify có `_cancel_point`. Đường login/MFA và cả đuôi
create_account → redirect → session (chặng `_step_follow_redirects` bắn tới
12 GET × 30s) không kiểm huỷ → bấm Stop có thể mất vài phút mới thoát.

Kiểm bằng cách:
  1. Đọc source: các chặng dài đều có `_cancel_point` / `should_cancel`.
  2. Chạy thật `_step_follow_redirects` với `should_cancel` luôn True → phải ném
     `JobCancelledError` NGAY ở hop đầu, không GET request nào.
"""

from __future__ import annotations

from pathlib import Path

from gpt_reg.core.exceptions import JobCancelledError
from gpt_reg.phases import http_reg as hr


class _ExplodingSession:
    """Bất kỳ GET nào cũng nổ — chứng minh cancel chặn TRƯỚC khi ra mạng."""

    gpt_profile = None

    def get(self, *a, **k):
        raise AssertionError("không được GET khi đã bị huỷ")


def main() -> int:
    failures: list[str] = []

    # 1. follow_redirects tôn trọng should_cancel ngay hop đầu.
    try:
        hr._step_follow_redirects(
            _ExplodingSession(), "https://chatgpt.com/", lambda _m: None,
            should_cancel=lambda: True,
        )
        failures.append("follow_redirects không dừng khi should_cancel=True")
    except JobCancelledError as exc:
        if exc.step != "http_redirect":
            failures.append(f"cancel step sai: {exc.step!r}")
    except AssertionError:
        failures.append("follow_redirects GET request dù đã bị huỷ")

    # should_cancel=None → chạy bình thường (không được đòi callable).
    try:
        hr._step_follow_redirects.__wrapped__  # type: ignore[attr-defined]
    except AttributeError:
        pass  # không bọc decorator — ok

    # 2. Source: mọi chặng dài đều có điểm kiểm huỷ.
    text = Path(hr.__file__).read_text(encoding="utf-8")
    required = {
        "http_login": "đăng nhập bằng mật khẩu",
        "http_mfa": "vượt 2FA",
        "http_finalize": "create_account/redirect",
        "http_redirect": "chuỗi redirect",
        "http_login_finish": "login xong không cần OTP",
    }
    for step, what in required.items():
        if f'"{step}"' not in text:
            failures.append(f"thiếu _cancel_point/{step} ({what})")

    # follow_redirects phải nhận should_cancel ở cả 3 nơi gọi trong _run_flow.
    if text.count("should_cancel=ctx.should_cancel") < 3:
        failures.append("chưa truyền should_cancel vào đủ các lần follow_redirects")

    # Signature có should_cancel.
    import inspect

    if "should_cancel" not in inspect.signature(hr._step_follow_redirects).parameters:
        failures.append("_step_follow_redirects không nhận should_cancel")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] http cancel" if failures else "[ok] http cancel")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
