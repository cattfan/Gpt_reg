"""Landing OTP lạnh phải resend để lấy login code, không gửi verification code."""

from __future__ import annotations

from gpt_reg.core.exceptions import HttpRegError
from gpt_reg.phases import http_reg as hr


def main() -> int:
    failures: list[str] = []
    sender = getattr(hr, "_send_initial_otp", None)
    if sender is None:
        failures.append("thiếu _send_initial_otp")
    else:
        calls: list[str] = []
        original_resend = hr._step_resend_otp
        original_send = hr._step_send_otp
        try:
            hr._step_resend_otp = lambda session, device_id, log: calls.append("resend") or True
            hr._step_send_otp = lambda session, device_id, log: calls.append("send")
            sender(
                object(), "device", lambda _line: None,
                cold_passwordless=True, reg_continue="",
            )
            if calls != ["resend"]:
                failures.append(f"cold OTP gọi sai endpoint: {calls}")

            calls.clear()
            sender(
                object(), "device", lambda _line: None,
                cold_passwordless=False, reg_continue="",
            )
            if calls != ["send"]:
                failures.append(f"register OTP gọi sai endpoint: {calls}")

            hr._step_resend_otp = lambda session, device_id, log: False
            try:
                sender(
                    object(), "device", lambda _line: None,
                    cold_passwordless=True, reg_continue="",
                )
            except HttpRegError as exc:
                if exc.step != "otp_send":
                    failures.append(f"resend lỗi có step={exc.step!r}")
            else:
                failures.append("resend login OTP lỗi nhưng bị fallback sang send")
        finally:
            hr._step_resend_otp = original_resend
            hr._step_send_otp = original_send

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] http cold otp" if failures else "[ok] http cold otp")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
