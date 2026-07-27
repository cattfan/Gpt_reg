"""Đo thời gian browser phase theo từng màn hình.

Browser phase mất 60–63s trong khi OTP chỉ chiếm ~9s; phần còn lại chưa rõ nằm ở
network, ở probe `detect_screen`, hay ở các `sleep` cứng. Module này ghi lại số
liệu đó để tối ưu dựa trên đo đạc chứ không phải phỏng đoán.
"""

from __future__ import annotations

import time
from typing import Callable


class FlowTimer:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.screen_seconds: dict[str, float] = {}
        self.screen_visits: dict[str, int] = {}
        self.detect_seconds = 0.0
        self.detect_calls = 0
        self._current: str | None = None
        self._current_since = self.started
        self._reported = False

    def record_detect(self, seconds: float) -> None:
        self.detect_seconds += seconds
        self.detect_calls += 1

    def enter(self, screen: str) -> None:
        """Ghi nhận màn hình hiện tại; tính giờ cho màn trước đó."""
        now = time.monotonic()
        if self._current is not None:
            self.screen_seconds[self._current] = (
                self.screen_seconds.get(self._current, 0.0) + now - self._current_since
            )
        if screen != self._current:
            self.screen_visits[screen] = self.screen_visits.get(screen, 0) + 1
        self._current = screen
        self._current_since = now

    def close(self) -> None:
        if self._current is not None:
            now = time.monotonic()
            self.screen_seconds[self._current] = (
                self.screen_seconds.get(self._current, 0.0) + now - self._current_since
            )
            self._current = None

    @property
    def total(self) -> float:
        return time.monotonic() - self.started

    def report(self, log: Callable[[str], None]) -> None:
        """In số liệu một lần duy nhất — gọi được từ `finally` mà không sợ trùng."""
        if self._reported:
            return
        self._reported = True
        self.close()
        parts = [
            f"{screen} {seconds:.1f}s x{self.screen_visits.get(screen, 0)}"
            for screen, seconds in sorted(
                self.screen_seconds.items(), key=lambda kv: kv[1], reverse=True
            )
            if seconds >= 0.05
        ]
        avg_ms = (self.detect_seconds / self.detect_calls * 1000) if self.detect_calls else 0.0
        log(f"[timing] screens: {' | '.join(parts) or '(none)'}")
        log(
            f"[timing] detect_screen {self.detect_seconds:.1f}s "
            f"/{self.detect_calls} calls (avg {avg_ms:.0f}ms), drive total {self.total:.1f}s"
        )
