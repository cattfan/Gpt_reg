"""Phân loại lỗi OTP HTTP và retry state phải fail-fast đúng nguyên nhân."""

from __future__ import annotations

from pathlib import Path

from gpt_reg.phases import http_reg as hr


def main() -> int:
    failures: list[str] = []
    classify = getattr(hr, "_classify_otp_failure", None)
    detail = getattr(hr, "_safe_error_detail", None)
    if classify is None:
        failures.append("thiếu _classify_otp_failure")
    else:
        cases = (
            (401, '{"error":{"code":"wrong_email_otp_code"}}', "wrong_code"),
            (409, '{"error":{"code":"invalid_state","message":"expired"}}', "invalid_state"),
            (429, '{"error":{"code":"rate_limit_exceeded"}}', "rate_limit"),
            (500, "upstream failure", "other"),
        )
        for status, body, wanted in cases:
            got = classify(status, body)
            if got != wanted:
                failures.append(f"OTP {status}/{body[:30]!r} => {got!r}, muốn {wanted!r}")

    if detail is None:
        failures.append("thiếu _safe_error_detail")
    else:
        raw = '{"error":{"code":"invalid_state","message":"expired for user@example.com token=secret123"}}'
        safe = detail(raw)
        if "invalid_state" not in safe or "expired" not in safe:
            failures.append(f"detail làm mất nguyên nhân: {safe!r}")
        if "user@example.com" in safe or "secret123" in safe:
            failures.append(f"detail làm lộ dữ liệu nhạy cảm: {safe!r}")

    source = (
        Path(__file__).resolve().parent.parent / "gpt_reg" / "phases" / "http_reg.py"
    ).read_text(encoding="utf-8")
    if 'exc.step != "invalid_state"' not in source:
        failures.append("HttpRegPhase chưa retry toàn flow khi auth state hết hạn")
    if 'f"OTP verify HTTP {status}", step="verify"' in source:
        failures.append("OTP verify vẫn bỏ mất error code/body")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] http otp errors" if failures else "[ok] http otp errors")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
