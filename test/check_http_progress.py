"""Guard the public HTTP registration progress contract."""

from __future__ import annotations

import inspect
import re


_MARKER = re.compile(r"\[(\d+)/10\]")


def _numbers(lines: list[str]) -> list[int]:
    values: list[int] = []
    for line in lines:
        match = _MARKER.search(line)
        if match:
            values.append(int(match.group(1)))
    return values


def main() -> int:
    import gpt_reg.phases.http_reg as module

    failures: list[str] = []
    source = inspect.getsource(module)
    if re.search(r"\[\d+/9\]", source):
        failures.append("old /9 HTTP progress marker remains")
    for index in range(1, 11):
        if f"[{index}/10]" not in source:
            failures.append(f"missing HTTP checkpoint {index}/10")
    for index in (6, 7, 8, 9):
        if f'[{index}/10] skipped:' not in source:
            failures.append(f"existing-session branch does not skip checkpoint {index}")

    full = [f"[http] [{index}/10] step" for index in range(1, 11)]
    skipped = [
        "[http] [1/10] prime",
        "[http] [2/10] csrf",
        "[http] [3/10] authorize",
        "[http] [4/10] oauth",
        "[http] [5/10] identify",
        "[http] [6/10] skipped: existing authenticated session",
        "[http] [7/10] skipped: OTP wait not required",
        "[http] [8/10] skipped: OTP verify not required",
        "[http] [9/10] skipped: existing account profile",
        "[http] [10/10] callback",
    ]
    expected = list(range(1, 11))
    if _numbers(full) != expected or _numbers(skipped) != expected:
        failures.append("progress parser does not observe a continuous 1..10 sequence")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] http progress" if failures else "[ok] http progress")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
