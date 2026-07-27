"""Kiểm tra ngân sách thời gian wall-clock cho một job.

Trước đây `FLOW_TIMEOUT=300s` chỉ bao vòng drive: `bootstrap` nằm ngoài, còn
`fill_about_you`/`wait_session_cookie`/`poll_code` là timeout riêng cộng thêm —
một job có thể giữ slot worker hơn 12 phút.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from gpt_reg.core.deadline import Deadline, DeadlineExceeded


def main() -> int:
    failures: list[str] = []

    d = Deadline(10.0)
    if not 9.9 <= d.remaining() <= 10.0:
        failures.append(f"remaining() ban đầu sai: {d.remaining()}")
    if d.expired():
        failures.append("vừa tạo đã expired")

    # slice không được vượt phần còn lại.
    if d.slice(100.0) > d.remaining() + 0.01:
        failures.append("slice() vượt quá thời gian còn lại")
    if d.slice(3.0) != 3.0:
        failures.append("slice() nhỏ hơn còn lại phải giữ nguyên")

    # Hết giờ: expired, slice trả về mức tối thiểu, raise đúng loại.
    expired = Deadline(0.05)
    time.sleep(0.1)
    if not expired.expired():
        failures.append("quá hạn mà expired() vẫn False")
    if expired.slice(60.0, minimum=2.0) != 2.0:
        failures.append("slice() khi hết giờ phải trả về minimum")
    try:
        expired.raise_if_expired("test")
        failures.append("raise_if_expired không ném lỗi")
    except DeadlineExceeded as exc:
        if "test" not in str(exc):
            failures.append(f"thông báo lỗi thiếu tên bước: {exc}")

    # Phase browser phải dùng ngân sách chung, không còn hằng số rời.
    source = Path(__file__).resolve().parent.parent / "gpt_reg" / "phases" / "browser" / "__init__.py"
    text = source.read_text(encoding="utf-8")
    if "JOB_BUDGET_S" not in text:
        failures.append("browser phase chưa có JOB_BUDGET_S")
    if "FLOW_TIMEOUT" in text:
        failures.append("vẫn còn FLOW_TIMEOUT cũ")
    if "deadline.raise_if_expired(\"bootstrap\")" not in text:
        failures.append("bootstrap không nằm trong ngân sách")
    # Không được còn timeout cứng cho các bước con dài.
    for hard in ("timeout_s=60.0", "timeout_s=90.0"):
        if hard in text:
            failures.append(f"còn timeout cứng {hard} — phải dùng deadline.slice()")
    if text.count("deadline.slice(") < 2:
        failures.append("các bước con chưa dùng deadline.slice()")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] deadline" if failures else "[ok] deadline")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
