"""Kiểm tra `otp.poll_code` chịu được lỗi mạng tạm thời của mail provider.

Gặp thật khi đo live: Graph qua proxy trả
`ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING]` và làm chết cả job dù chỉ là
lỗi chớp nhoáng. Lỗi không phải transient thì vẫn phải nổ ngay.
"""

from __future__ import annotations

import asyncio

from gpt_reg.phases.browser import otp

TRANSIENT_CASES = (
    (ConnectionError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred"), True),
    (TimeoutError("timed out"), True),
    (RuntimeError("HTTP 503 Service Unavailable"), True),
    (RuntimeError("connection reset by peer"), True),
    (ValueError("combo không hợp lệ"), False),
    (KeyError("missing"), False),
)


class _FlakyMail:
    def __init__(self, failures: int) -> None:
        self.calls = 0
        self._failures = failures

    def wait_for_otp(self, **_kwargs: object) -> str:
        self.calls += 1
        if self.calls <= self._failures:
            raise ConnectionError("[SSL: UNEXPECTED_EOF_WHILE_READING]")
        return "123456"


class _BrokenMail:
    def wait_for_otp(self, **_kwargs: object) -> str:
        raise ValueError("combo hỏng")


async def _run() -> int:
    failed = 0
    for exc, expected in TRANSIENT_CASES:
        if otp._is_transient(exc) != expected:
            failed += 1
            print(f"[fail] _is_transient({exc!r}) != {expected}")

    real_sleep = asyncio.sleep

    async def _no_sleep(_seconds: float) -> None:
        await real_sleep(0)

    asyncio.sleep = _no_sleep  # type: ignore[assignment]
    try:
        mail = _FlakyMail(failures=2)
        code, _ = await otp.poll_code(
            mail,
            email="a@b.c",
            since=otp.utc_now(),
            timeout_s=30,
            poll_interval_s=0.1,
            log=lambda _s: None,
            consumed=set(),
        )
        if code != "123456" or mail.calls != 3:
            failed += 1
            print(f"[fail] retry: code={code} calls={mail.calls} (want 123456 / 3)")

        try:
            await otp.poll_code(
                _BrokenMail(),
                email="a@b.c",
                since=otp.utc_now(),
                timeout_s=5,
                poll_interval_s=0.1,
                log=lambda _s: None,
                consumed=set(),
            )
        except ValueError:
            pass
        else:
            failed += 1
            print("[fail] lỗi không transient phải propagate")
    finally:
        asyncio.sleep = real_sleep  # type: ignore[assignment]

    if failed:
        print(f"[fail] otp retry ({failed} lỗi)")
    else:
        print("[ok] otp retry")
    return failed


if __name__ == "__main__":
    raise SystemExit(1 if asyncio.run(_run()) else 0)
