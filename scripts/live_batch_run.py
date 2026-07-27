"""Sequential live signup+2FA with per-account timing report."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gpt_reg.mail.providers import build_request_from_combo
from gpt_reg.models import SignupRequest
from gpt_reg.signup import run_signup

_TIMING_RE = re.compile(
    r"\[timing\]\s+(browser|http|mfa|total)\s+([\d.]+)s",
)


def _parse_timings(lines: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in lines:
        m = _TIMING_RE.search(line)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def main() -> int:
    batch_path = ROOT / "runtime" / "live_batch.txt"
    if not batch_path.exists():
        print(f"missing {batch_path}", file=sys.stderr)
        return 1

    combos = [ln.strip() for ln in batch_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    report_path = ROOT / "runtime" / "live_batch_report.jsonl"
    results: list[dict] = []

    for i, combo in enumerate(combos, 1):
        email, password = build_request_from_combo(combo)
        log_lines: list[str] = []

        def log(msg: str) -> None:
            log_lines.append(msg)
            print(f"[{i}/{len(combos)}] {msg}", flush=True)

        log(f"=== START {email} ===")
        t0 = time.monotonic()
        req = SignupRequest(
            email=email,
            password=password,
            outlook_combo=combo,
            headless=False,
            mail_provider="outlook",
            reg_mode="browser",
            otp_poll_interval_seconds=2.0,
        )
        result = run_signup(req, log=log, with_2fa=True)
        elapsed = time.monotonic() - t0
        timings = _parse_timings(log_lines)
        row = {
            "index": i,
            "email": email,
            "ok": result.ok,
            "error": result.error,
            "exit_code": result.exit_code,
            "session_path": result.session_path,
            "mfa_activated": result.mfa_activated,
            "wall_seconds": round(elapsed, 1),
            "timings": timings,
        }
        results.append(row)
        with report_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        log(f"=== END ok={result.ok} wall={elapsed:.1f}s ===")

    ok_count = sum(1 for r in results if r["ok"])
    print(f"\nBatch done: {ok_count}/{len(results)} OK -> {report_path}")
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
