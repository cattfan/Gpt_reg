"""Định tuyến đường đăng nhập / cứu account cho reg_mode="http".

Đây là đường retry cho những acc fail giữa chừng, và nó có ba trạng thái server
KHÁC NHAU mà lúc đầu bị gộp thành một cờ `login_mode` duy nhất:

  1. `/log-in/password`     → account đủ hồ sơ, xác thực bằng mật khẩu.
  2. `/email-verification`  → server muốn MÃ, không phải mật khẩu. Gọi
     `password/verify` ở đây trả **409 invalid_state** (đã đo thật trên
     TouchRockett622@hotmail.com), làm mất luôn cơ hội cứu account.
  3. sau khi verify mã: account đăng ký nửa chừng CHƯA có name/birthdate nên
     vẫn phải gọi `create_account`, còn account đủ hồ sơ thì gọi lại bị 400
     `user_already_exists`. Không phân biệt được bằng `page.type` (server trả
     'about_you' cho cả hai) — phải thử lấy session trước rồi mới quyết.

Test đọc source vì các nhánh này nằm giữa một flow toàn request mạng, không thể
gọi thẳng `_run_flow` mà không dựng cả server OpenAI. Đổi lại nó bắt được đúng
kiểu lỗi đã xảy ra: nhánh `otp` rơi vào `password/verify`.
"""

from __future__ import annotations

import re
from pathlib import Path

from gpt_reg.phases.http_reg import classify_landing


LANDING_CASES = [
    ("https://auth.openai.com/log-in/password?flow=x", "login"),
    ("https://auth.openai.com/create-account/password", "register"),
    ("https://auth.openai.com/email-verification", "otp"),
    ("https://auth.openai.com/api/accounts/email-otp/send", "otp"),
    ("https://auth.openai.com/about-you", "unknown"),
    ("", "unknown"),
]


def _guard_of(text: str, needle: str) -> str | None:
    """Gộp MỌI điều kiện `if` bao lấy dòng chứa `needle` (theo thụt lề).

    Lấy tất cả các tầng chứ không chỉ tầng gần nhất: lời gọi có thể nằm sâu
    trong một vòng lặp hoặc một `if` phụ, lúc đó điều kiện định tuyến thật nằm ở
    tầng ngoài.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if needle not in line:
            continue
        guards: list[str] = []
        indent = len(line) - len(line.lstrip())
        for j in range(i - 1, -1, -1):
            prev = lines[j]
            if not prev.strip():
                continue
            prev_indent = len(prev) - len(prev.lstrip())
            if prev_indent >= indent:
                continue
            indent = prev_indent
            stripped = prev.strip()
            if stripped.startswith("if ") and stripped.endswith(":"):
                guards.append(stripped[3:-1].strip())
            if prev_indent == 0:
                break
        if guards:
            return " AND ".join(reversed(guards))
    return None


def main() -> int:
    failures: list[str] = []

    for url, want in LANDING_CASES:
        got = classify_landing(url)
        if got != want:
            failures.append(f"classify_landing({url!r}) = {got!r}, cần {want!r}")

    source = Path(__file__).resolve().parent.parent / "gpt_reg" / "phases" / "http_reg.py"
    text = source.read_text(encoding="utf-8")

    # Nhánh gọi password/verify phải có ĐIỀU KIỆN loại trừ landing=otp. Bản cũ là
    # `if login_mode:` trơn — chính là lỗi 409 invalid_state.
    cond = _guard_of(text, "login = _step_login_password(")
    if cond is None:
        failures.append("không tìm thấy nhánh gọi _step_login_password")
    elif "login_kind" not in cond:
        failures.append(
            f"_step_login_password gọi khi `{cond}` — thiếu kiểm tra login_kind, "
            "landing=otp sẽ lại bị 409 invalid_state"
        )

    # landing=otp phải set login_kind="otp" + needs_otp_after_login, KHÔNG được
    # rơi vào nhánh mật khẩu.
    otp_branch = re.search(r'landing_kind == "otp":\n((?:.*\n)*?)\n', text)
    if not otp_branch:
        failures.append("thiếu nhánh landing_kind == \"otp\"")
    else:
        body = otp_branch.group(1)
        for need in ('login_kind = "otp"', "needs_otp_after_login = True", "login_mode = True"):
            if need not in body:
                failures.append(f"nhánh otp thiếu `{need}`")

    # Account nửa chừng: phải còn đường gọi create_account khi login_mode=True.
    if "needs_details" not in text:
        failures.append(
            "login_mode bỏ qua create_account vô điều kiện — acc đăng ký nửa chừng "
            "sẽ kẹt mãi ở /about-you"
        )
    else:
        # Và phải kích hoạt bằng "không lấy được session", không phải bằng cách
        # đoán từ page.type — server trả page='about_you' cả khi account đã đủ
        # hồ sơ (lúc đó create_account trả 400 user_already_exists).
        guard = _guard_of(text, "needs_details = True")
        if guard is None or "session_token" not in guard:
            failures.append(
                f"nhánh create_account cứu acc kích hoạt bằng `{guard}` — phải dựa "
                "trên việc không lấy được session, không phải đoán page.type"
            )

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] http login" if failures else "[ok] http login")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
