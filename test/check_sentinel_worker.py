"""Kiểm tra SentinelNodeWorker không treo vô hạn khi Node bận vòng lặp đồng bộ.

`readline()` trên pipe không có timeout. Nếu tin vào nó, một Node process còn
sống nhưng không trả lời (sdk.js chạy dưới eval với setTimeout bị override thành
đồng bộ → event loop chết) sẽ khoá cứng `run_action` trong khi giữ `self._lock`,
treo cả job đăng ký và không rơi được về Python PoW.
"""

from __future__ import annotations

import shutil
import time

from gpt_reg.sentinel.quickjs import SentinelNodeWorker


def main() -> int:
    if not shutil.which("node"):
        print("[skip] sentinel worker (chưa cài Node)")
        return 0

    failures: list[str] = []

    # Worker giả: Node đọc stdin rồi kẹt vòng lặp đồng bộ, không bao giờ trả lời.
    hang_js = "process.stdin.resume(); process.stdin.on('data', () => { while (true) {} });"
    worker = SentinelNodeWorker(node_path="node", script_file=__file__, log=lambda _m: None)
    worker._proc = __import__("subprocess").Popen(
        ["node", "-e", hang_js],
        stdin=-1, stdout=-1, stderr=__import__("subprocess").DEVNULL,
        text=True, bufsize=1,
    )

    started = time.monotonic()
    try:
        worker.run_action(action="requirements", sdk_file=__file__, payload={}, timeout_ms=2000)
        failures.append("run_action phải raise khi worker treo")
    except RuntimeError as exc:
        if "timeout" not in str(exc).lower():
            failures.append(f"raise sai loại: {exc}")
    except Exception as exc:
        failures.append(f"raise sai loại: {type(exc).__name__}: {exc}")
    elapsed = time.monotonic() - started

    # Deadline = max(10, 2 + 5) = 10s; cho dư 8s để máy chậm vẫn qua.
    if elapsed > 18.0:
        failures.append(f"timeout quá muộn: {elapsed:.1f}s")

    # Process treo phải bị giết để lần sau respawn được.
    if worker._proc is not None:
        failures.append("process treo không bị dọn")

    # close() sau đó không được deadlock trên lock.
    close_started = time.monotonic()
    worker.close()
    if time.monotonic() - close_started > 5.0:
        failures.append("close() bị treo trên lock")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] sentinel worker" if failures else f"[ok] sentinel worker (timeout {elapsed:.1f}s)")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
