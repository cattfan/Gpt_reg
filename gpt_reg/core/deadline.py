"""Ngân sách thời gian tính theo wall-clock cho cả một job.

Trước đây `FLOW_TIMEOUT = 300s` chỉ bao vòng lặp drive: `bootstrap` nằm **ngoài**,
còn `fill_about_you` (60s), `wait_session_cookie` (60s), `poll_code` (180s) là
timeout riêng **cộng thêm**. Một job browser vì thế có thể giữ slot worker và
~300 MB RAM hơn 12 phút, trong khi nút Stop không cắt được các timeout con.

`Deadline` là một mốc duy nhất tính từ lúc job bắt đầu. Mọi bước con hỏi
`remaining()` thay vì tự đặt hằng số, nên tổng thời gian không bao giờ vượt ngân
sách dù có bao nhiêu bước.
"""

from __future__ import annotations

import time


class DeadlineExceeded(Exception):
    """Hết ngân sách thời gian của job."""


class Deadline:
    def __init__(self, budget_s: float):
        self.budget_s = budget_s
        self.started = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def remaining(self) -> float:
        return max(0.0, self.budget_s - self.elapsed)

    def expired(self) -> bool:
        return self.remaining() <= 0

    def slice(self, want_s: float, *, minimum: float = 1.0) -> float:
        """Thời gian cấp cho một bước con: không quá `want_s`, không quá phần còn lại.

        `minimum` để bước con vẫn có cơ hội chạy thay vì nhận 0 và fail ngay —
        caller nên kiểm tra `expired()` trước nếu muốn dừng hẳn.
        """
        return max(minimum, min(want_s, self.remaining()))

    def raise_if_expired(self, step: str) -> None:
        if self.expired():
            raise DeadlineExceeded(
                f"hết ngân sách {self.budget_s:.0f}s tại bước {step} "
                f"(đã dùng {self.elapsed:.0f}s)"
            )

    def __repr__(self) -> str:  # pragma: no cover - chỉ để debug
        return f"<Deadline {self.elapsed:.0f}/{self.budget_s:.0f}s>"
