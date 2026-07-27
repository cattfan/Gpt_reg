"""Chạy check plan nhiều luồng — song song với `reg_manager`.

Khác đăng ký: mỗi check chỉ là vài request HTTP nên đẩy được luồng cao (tới 200).
Nút cổ chai thực tế là sentinel pool (login cần một token QuickJS) — pool 8 Node
worker dùng chung, các luồng vượt quá sẽ xếp hàng lấy token. Dùng lại đúng khuôn
ghi-qua-lock + SSE của reg_manager.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Any, Callable

from gpt_reg.checker.combo import CheckCombo, CheckComboError
from gpt_reg.checker.flow import CheckError, check_account
from gpt_reg.core.exceptions import JobCancelledError
from gpt_reg.proxy.pool import ProxyPool

CHECK_CONCURRENCY_CHOICES = (1, 2, 5, 10, 20, 50, 100, 200)
MAX_CONCURRENCY_CHECK = 200

# kind của CheckError → status hiển thị. Gộp về ít trạng thái: live / die /
# onboarding / error. `die` = login bị từ chối chắc chắn; `error` = tạm thời
# hoặc combo thiếu thông tin (thêm 2fa/fullmail là chạy lại được).
_KIND_TO_STATUS = {
    "die": "die",
    "not_found": "die",
    "onboarding": "onboarding",
    "need_2fa": "error",
    "need_fullmail": "error",
    "error": "error",
}


def clamp_check_concurrency(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1
    if n in CHECK_CONCURRENCY_CHOICES:
        return min(n, MAX_CONCURRENCY_CHECK)
    # Không đúng nấc → về nấc gần nhất không vượt trần.
    allowed = [c for c in CHECK_CONCURRENCY_CHOICES if c <= MAX_CONCURRENCY_CHECK]
    return min(allowed, key=lambda c: abs(c - n)) if allowed else 1


class CheckManager:
    kind = "check"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_workers = 0
        self._stop_all = threading.Event()
        self._cancelled: set[str] = set()
        self._running: set[str] = set()
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    # ── events ──────────────────────────────────────────────────────────────
    def subscribe(self, fn: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(fn)

    def unsubscribe(self, fn: Callable[[dict[str, Any]], None]) -> None:
        try:
            self._listeners.remove(fn)
        except ValueError:
            pass

    def _emit(self, event: dict[str, Any]) -> None:
        event.setdefault("scope", "check")
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:
                pass

    # ── cancel ────────────────────────────────────────────────────────────
    @property
    def running(self) -> bool:
        with self._lock:
            return self._active_workers > 0

    def stop_all(self) -> None:
        self._stop_all.set()
        with self._lock:
            self._cancelled.update(self._running)

    def stop_check(self, check_id: str) -> None:
        with self._lock:
            self._cancelled.add(check_id)

    def _should_cancel(self, check_id: str) -> bool:
        if self._stop_all.is_set():
            return True
        with self._lock:
            return check_id in self._cancelled

    # ── batch ───────────────────────────────────────────────────────────────
    def start_batch(
        self, *, combos: list[str], checks_repo, proxy_pool_text: str,
        rotation_mode: str = "round_robin", concurrency: int = 1,
        check_ids: list[str] | None = None,
    ) -> list[str]:
        if self.running:
            return []
        self._stop_all.clear()
        workers = clamp_check_concurrency(concurrency)

        ids: list[str] = []
        if check_ids is not None:
            ids = list(check_ids)
            for cid in ids:
                with self._lock:
                    self._cancelled.discard(cid)
                checks_repo.update(
                    cid, status="queued", error=None, plan=None, plan_detail=None,
                    has_subscription=0, expires_at=None, deactivated=0,
                    seconds=None, started_at=None, finished_at=None,
                )
        else:
            # Kiểm TẤT CẢ combo trước khi ghi — một dòng hỏng ở giữa không được để
            # lại check kẹt `queued` khoá nút Run (bài học từ reg_manager).
            parsed: list[CheckCombo] = []
            for index, line in enumerate(combos, 1):
                if not line.strip():
                    continue
                try:
                    parsed.append(CheckCombo.parse(line, line_number=index))
                except CheckComboError:
                    raise
            for combo in parsed:
                cid = uuid.uuid4().hex
                checks_repo.create({
                    "id": cid,
                    "email": combo.email,
                    "combo": f"{combo.email}|{combo.password}|{combo.totp_secret}"
                             + (f"|{combo.full_combo}" if combo.has_full_combo else ""),
                    "status": "queued",
                    "created_at": time.time(),
                })
                ids.append(cid)

        if not ids:
            return []

        pending: queue.Queue[str] = queue.Queue()
        for cid in ids:
            pending.put(cid)
        workers = min(workers, len(ids))
        self._emit({"type": "batch", "status": "running", "concurrency": workers, "checks": len(ids)})
        with self._lock:
            self._active_workers = workers
        pool = ProxyPool.from_multiline(proxy_pool_text, rotation_mode=rotation_mode or "round_robin")
        for i in range(workers):
            threading.Thread(
                target=self._worker, args=(pending, checks_repo, pool),
                name=f"check-worker-{i}", daemon=True,
            ).start()
        return ids

    def _worker(self, pending: "queue.Queue[str]", checks_repo, pool: ProxyPool) -> None:
        try:
            while True:
                try:
                    cid = pending.get_nowait()
                except queue.Empty:
                    return
                try:
                    if self._stop_all.is_set() or self._should_cancel(cid):
                        self._finish(checks_repo, cid, status="cancelled", error="stopped")
                        continue
                    row = checks_repo.get(cid)
                    if row:
                        self._run_one(checks_repo, row, pool)
                except Exception as exc:
                    self._finish(checks_repo, cid, status="error", error=f"{type(exc).__name__}: {exc}")
                finally:
                    pending.task_done()
        finally:
            with self._lock:
                self._active_workers -= 1
                last = self._active_workers <= 0
            if last:
                self._emit({"type": "batch", "status": "idle"})

    def _run_one(self, checks_repo, row: dict[str, Any], pool: ProxyPool) -> None:
        cid = str(row["id"])
        with self._lock:
            self._running.add(cid)
        started = time.time()
        checks_repo.update(cid, status="running", started_at=started)
        self._emit({"type": "check", "check_id": cid, "status": "running", "email": row["email"]})

        def log(line: str) -> None:
            # Check nhanh nên không lưu log từng dòng vào DB; chỉ đẩy SSE để xem live.
            self._emit({"type": "check_log", "check_id": cid, "line": line})

        from gpt_reg.sentinel.pool import get_pool

        sent_pool = get_pool()
        worker = None
        try:
            combo = CheckCombo.parse(row["combo"])
            proxy = pool.acquire_url()
            try:
                worker = sent_pool.acquire(log)
            except Exception:
                worker = None
            result = check_account(
                combo, proxy, log, worker=worker,
                should_cancel=lambda: self._should_cancel(cid),
            )
            self._finish(
                checks_repo, cid, status="live",
                plan=result.get("plan"), plan_detail=result.get("plan_detail"),
                has_subscription=1 if result.get("has_subscription") else 0,
                expires_at=_as_text(result.get("expires_at")),
                mfa_enabled=1 if result.get("mfa_enabled") else 0,
                deactivated=1 if result.get("deactivated") else 0,
                email=result.get("email") or row["email"],
                seconds=round(time.time() - started, 1),
            )
        except JobCancelledError:
            self._finish(checks_repo, cid, status="cancelled", error="stopped",
                         seconds=round(time.time() - started, 1))
        except CheckError as exc:
            self._finish(
                checks_repo, cid, status=_KIND_TO_STATUS.get(exc.kind, "error"),
                error=str(exc), seconds=round(time.time() - started, 1),
            )
        finally:
            sent_pool.release(worker)
            with self._lock:
                self._running.discard(cid)

    def _finish(self, checks_repo, check_id: str, *, status: str, **fields: Any) -> None:
        checks_repo.update(check_id, status=status, finished_at=time.time(), **fields)
        with self._lock:
            self._cancelled.discard(check_id)
            self._running.discard(check_id)
        self._emit({"type": "check", "check_id": check_id, "status": status, **fields})


def _as_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


_MANAGER: CheckManager | None = None


def get_check_manager() -> CheckManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = CheckManager()
    return _MANAGER
