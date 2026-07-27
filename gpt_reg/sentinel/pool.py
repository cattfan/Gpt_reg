"""Pool Node worker dùng chung cho sentinel.

Trước đây mỗi job tạo một `SentinelNodeWorker` riêng. Đo thực tế: **54 MB mỗi
Node process** — chạy 200 luồng là **10.5 GB** chỉ để sinh token, trong khi phần
lớn thời gian process nằm không (mỗi job chỉ cần 2 lần sinh token, mỗi lần ~3s).

Dùng chung một worker duy nhất thì lại quá chậm: `run_action` serialize bằng lock
nội bộ, 200 job × 2 token × 3s ≈ 20 phút xếp hàng.

Nên đây là pool cố định: N worker, job nào rảnh thì lấy. N mặc định theo số CPU
(sinh token là proof-of-work, CPU-bound) và chặn trần để không nuốt hết RAM.
"""

from __future__ import annotations

import os
import queue
import threading
from typing import Any, Callable

# Mỗi worker ~54 MB. 8 worker ≈ 430 MB — đủ song song mà không phình bộ nhớ.
DEFAULT_POOL_SIZE = 8
MAX_POOL_SIZE = 16


def _default_size() -> int:
    env = os.getenv("OPENAI_SENTINEL_POOL_SIZE", "").strip()
    if env.isdigit() and int(env) > 0:
        return min(int(env), MAX_POOL_SIZE)
    cpu = os.cpu_count() or 4
    return max(2, min(cpu // 2, DEFAULT_POOL_SIZE))


class SentinelWorkerPool:
    """Pool lười khởi tạo: chỉ spawn Node khi thật sự có job cần."""

    def __init__(self, size: int | None = None) -> None:
        self._size = size or _default_size()
        self._idle: "queue.Queue[Any]" = queue.Queue()
        self._created = 0
        self._lock = threading.Lock()
        self._closed = False

    @property
    def size(self) -> int:
        return self._size

    def _new_worker(self, log: Callable[[str], None]) -> Any:
        from gpt_reg.sentinel.quickjs import create_worker

        return create_worker(log)

    def acquire(self, log: Callable[[str], None]) -> Any:
        """Lấy một worker. None nghĩa là không dùng được QuickJS (thiếu Node/script)."""
        if self._closed:
            return None
        try:
            return self._idle.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            if self._created < self._size:
                worker = self._new_worker(log)
                if worker is None:
                    return None
                self._created += 1
                return worker
        # Đã đủ worker — chờ tới lượt thay vì spawn thêm.
        try:
            return self._idle.get(timeout=120)
        except queue.Empty:
            return None

    def release(self, worker: Any) -> None:
        if worker is None:
            return
        if self._closed:
            try:
                worker.close()
            except Exception:
                pass
            return
        self._idle.put(worker)

    def close(self) -> None:
        self._closed = True
        while True:
            try:
                worker = self._idle.get_nowait()
            except queue.Empty:
                break
            try:
                worker.close()
            except Exception:
                pass
        with self._lock:
            self._created = 0


_pool: SentinelWorkerPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> SentinelWorkerPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = SentinelWorkerPool()
        return _pool


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
