"""Timing phải phản ánh engine thật, không gắn nhãn Browser cho HTTP thuần."""

from __future__ import annotations

from gpt_reg import signup


def main() -> int:
    failures: list[str] = []
    timing = getattr(signup, "_registration_timing", None)
    if timing is None:
        failures.append("thiếu _registration_timing")
    else:
        pure_http = timing("http", 25.94, 0.04)
        if pure_http != {"browser_seconds": None, "http_seconds": 26.0}:
            failures.append(f"timing HTTP sai: {pure_http}")
        browser = timing("browser", 42.24, 1.26)
        if browser != {"browser_seconds": 42.2, "http_seconds": 1.3}:
            failures.append(f"timing Browser sai: {browser}")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] signup timing" if failures else "[ok] signup timing")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
