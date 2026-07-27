"""Kiểm tra pool Node worker dùng chung cho sentinel.

Trước đây mỗi job spawn một Node riêng. Đo thực tế **54 MB/process** → 200 luồng
là 10.5 GB chỉ để sinh token. Pool giới hạn số process và tái dùng.
"""

from __future__ import annotations

import shutil

from gpt_reg.sentinel.pool import MAX_POOL_SIZE, SentinelWorkerPool, close_pool, get_pool


class _FakeWorker:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakePool(SentinelWorkerPool):
    """Pool không spawn Node thật — chỉ kiểm tra logic mượn/trả."""

    def __init__(self, size: int) -> None:
        super().__init__(size=size)
        self.spawned = 0

    def _new_worker(self, log):
        self.spawned += 1
        return _FakeWorker()


def main() -> int:
    failures: list[str] = []

    pool = _FakePool(size=3)

    # Mượn/trả tuần tự phải tái dùng đúng một worker.
    for _ in range(10):
        worker = pool.acquire(lambda _m: None)
        pool.release(worker)
    if pool.spawned != 1:
        failures.append(f"mượn/trả tuần tự spawn {pool.spawned} worker (muốn 1)")

    # Giữ đồng thời không được vượt size.
    held = [pool.acquire(lambda _m: None) for _ in range(3)]
    if pool.spawned != 3:
        failures.append(f"giữ 3 worker nhưng spawn {pool.spawned}")
    if any(w is None for w in held):
        failures.append("không mượn đủ số worker trong giới hạn")
    for worker in held:
        pool.release(worker)

    # close() phải đóng mọi worker đang rảnh.
    workers = [pool.acquire(lambda _m: None) for _ in range(3)]
    for worker in workers:
        pool.release(worker)
    pool.close()
    if not all(w.closed for w in workers):
        failures.append("close() bỏ sót worker")

    # Sau khi đóng, mượn trả None chứ không spawn thêm.
    if pool.acquire(lambda _m: None) is not None:
        failures.append("pool đã đóng vẫn cấp worker")

    # Pool toàn cục: cùng một instance, size hợp lệ.
    a, b = get_pool(), get_pool()
    if a is not b:
        failures.append("get_pool() không dùng chung instance")
    if not 1 <= a.size <= MAX_POOL_SIZE:
        failures.append(f"pool size ngoài khoảng: {a.size}")
    close_pool()

    # http_reg phải mượn từ pool, không tự create_worker mỗi job.
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "gpt_reg" / "phases" / "http_reg.py"
    text = source.read_text(encoding="utf-8")
    if "sentinel.pool import get_pool" not in text:
        failures.append("http_reg không dùng pool")
    if "create_worker(log)" in text:
        failures.append("http_reg vẫn spawn worker riêng mỗi job")

    if not shutil.which("node"):
        print("[skip] phần Node thật (chưa cài Node)")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] sentinel pool" if failures else "[ok] sentinel pool")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
