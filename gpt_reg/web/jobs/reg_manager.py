"""Chạy batch đăng ký cho Web UI, có đa luồng.

Một pool `concurrency` worker thread rút job từ hàng đợi. Huỷ là **hợp tác**:
manager bật cờ, phase tự thoát ở điểm kiểm tra gần nhất
(`RunContext.raise_if_cancelled`) rồi đóng browser qua `async with`. Không kill
thread — kill sẽ để lại process Camoufox mồ côi và file session ghi dở.

Mỗi job có RunContext riêng (proxy riêng, cờ huỷ riêng) nên chạy song song an
toàn. `ProxyPool` có lock nội bộ; ghi SQLite được serialize trong repository.
"""

from __future__ import annotations

import queue
import re
import threading
import time
import uuid
from typing import Any, Callable

from gpt_reg.browser.fingerprint import (
    materialize_browser_fingerprint,
    parse_browser_fingerprint,
    serialize_browser_fingerprint,
)
from gpt_reg.core.context import RunContext
from gpt_reg.fingerprint import get_profile, identity_id, new_seed, profile_for_seed
from gpt_reg.mail.providers import build_request_from_combo
from gpt_reg.mail.rental import MailRental, MailRentalProvider
from gpt_reg.models import SignupRequest
from gpt_reg.profile_identity import generate_profile_identity
from gpt_reg.proxy.pool import ProxyPool
from gpt_reg.signup import _build_context, run_signup
from gpt_reg.web.jobs.rental_coordinator import (
    RentalCoordinator,
    RentalMailboxProvider,
    _is_expired,
)

FINISHED_STATUSES = ("success", "error", "cancelled")

_LOG_OTP_RE = re.compile(r"(?i)(\bOTP\s+)\d{4,10}\b")
_LOG_SECRET_ASSIGN_RE = re.compile(
    r"(?i)(?P<prefix>[\"']?(?:access[_-]?token|refresh[_-]?token|session[_-]?token|"
    r"factor_id|password|secret|token|code|otp|screenshot|session_path)[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"']?)(?P<value>[^\"'\s,;}\]]+)(?P=quote)\]?"
)
_LOG_SESSION_PATH_RE = re.compile(r"(?i)^(\[signup\]\s+session saved)(?:\s+.+)?$")
_LOG_URL_QUERY_RE = re.compile(r"(?i)(https?://[^\s?#]+)\?[^\s#]*(?:#[^\s]*)?")
_LOG_WINDOWS_PATH_RE = re.compile(r"(?i)(?<![\w])(?:[a-z]:\\)[^\s]+")


def _replace_secret_assignment(match: re.Match[str]) -> str:
    quote = match.group("quote")
    return f"{match.group('prefix')}{quote}[redacted]{quote}"


def sanitize_job_log_line(line: str) -> str:
    """Remove one-time credentials and local paths before persistence or SSE."""
    value = str(line)
    value = _LOG_URL_QUERY_RE.sub(r"\1", value)
    value = _LOG_OTP_RE.sub(r"\1[redacted]", value)
    value = _LOG_SECRET_ASSIGN_RE.sub(_replace_secret_assignment, value)
    value = _LOG_SESSION_PATH_RE.sub(r"\1", value)
    value = _LOG_WINDOWS_PATH_RE.sub("[path]", value)
    return value


class InvalidComboError(ValueError):
    """Một dòng combo không parse được — kèm số dòng để UI chỉ đúng chỗ."""

    def __init__(self, line_number: int, reason: str):
        super().__init__(f"dòng {line_number}: {reason}")
        self.line_number = line_number
        self.reason = reason

# Mức luồng cho UI. Trần khác nhau theo chế độ vì chi phí mỗi job khác hẳn:
# một Camoufox tốn ~300 MB RAM, còn một session curl_cffi chỉ ~10 MB.
CONCURRENCY_CHOICES = (1, 2, 5, 10, 20, 50, 100, 200)
MAX_CONCURRENCY_BROWSER = 10
MAX_CONCURRENCY_HTTP = 200

def clamp_concurrency(value: Any, reg_mode: str, fallback_enabled: bool = False) -> int:
    """Ép mức luồng về khoảng hợp lệ cho chế độ đang chạy."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 1
    # Fallback đối xứng nghĩa là bất kỳ primary nào cũng có thể mở Browser.
    # Luôn dùng trần Browser để tránh một batch HTTP 200 luồng sinh 200 Camoufox.
    ceiling = (
        MAX_CONCURRENCY_BROWSER
        if reg_mode == "browser" or fallback_enabled
        else MAX_CONCURRENCY_HTTP
    )
    return max(1, min(n, ceiling))


class RegJobManager:
    kind = "reg"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_workers = 0
        self._stop_all = threading.Event()
        self._cancelled_jobs: set[str] = set()
        self._running_jobs: set[str] = set()
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    # ── events ────────────────────────────────────────────────────────────

    def subscribe(self, fn: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(fn)

    def unsubscribe(self, fn: Callable[[dict[str, Any]], None]) -> None:
        """Bỏ listener khi client SSE ngắt — không gỡ thì mỗi lần reload tab lại
        thêm một listener sống mãi."""
        try:
            self._listeners.remove(fn)
        except ValueError:
            pass

    def _emit(self, event: dict[str, Any]) -> None:
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:
                pass

    def snapshot_for_sse(self, jobs: list[dict[str, Any]]) -> dict[str, Any]:
        public_fields = (
            "id",
            "email",
            "mail_mode",
            "reg_mode",
            "status",
            "error",
            "mfa_activated",
            "browser_seconds",
            "http_seconds",
            "mfa_seconds",
            "created_at",
            "started_at",
            "finished_at",
            "registered_at",
            "profile_region",
        )
        snapshot = {
            "kind": self.kind,
            "jobs": [
                {key: row.get(key) for key in public_fields if key in row}
                for row in jobs
            ],
        }
        for row in snapshot["jobs"]:
            if row.get("error") is not None:
                row["error"] = sanitize_job_log_line(str(row["error"]))
        return snapshot

    # ── cancellation ──────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        with self._lock:
            return self._active_workers > 0

    def stop_all(self) -> None:
        """Huỷ mọi job đang chạy và bỏ qua phần còn lại trong hàng đợi."""
        self._stop_all.set()
        with self._lock:
            self._cancelled_jobs.update(self._running_jobs)

    def stop_job(self, job_id: str) -> None:
        with self._lock:
            self._cancelled_jobs.add(job_id)

    def _should_cancel(self, job_id: str) -> bool:
        if self._stop_all.is_set():
            return True
        with self._lock:
            return job_id in self._cancelled_jobs

    # ── batch ─────────────────────────────────────────────────────────────

    def start_batch(
        self,
        *,
        combos: list[str],
        headless: bool,
        jobs_repo,
        ctx: RunContext | None = None,
        with_2fa: bool = False,
        reg_mode: str = "browser",
        fallback_enabled: bool = False,
        concurrency: int = 1,
        profile_region: str = "vi",
        job_ids: list[str] | None = None,
        proxy_pool: ProxyPool | None = None,
    ) -> list[str]:
        """Tạo job rồi chạy bằng pool `concurrency` worker.

        `job_ids` cho phép chạy lại job có sẵn (retry) thay vì tạo id mới, nhờ
        đó lịch sử log và vị trí trong danh sách được giữ nguyên.
        """
        if self.running:
            return []
        self._stop_all.clear()
        workers = clamp_concurrency(concurrency, reg_mode, fallback_enabled)

        ids: list[str] = []
        if job_ids is not None:
            ids = list(job_ids)
            for job_id in ids:
                jobs_repo.ensure_fingerprint_identity(job_id)
                with self._lock:
                    self._cancelled_jobs.discard(job_id)
                jobs_repo.clear_logs(job_id)
                jobs_repo.update(
                    job_id,
                    reg_mode=reg_mode,
                    status="queued",
                    error=None,
                    session_path=None,
                    mfa_activated=0,
                    browser_seconds=None,
                    http_seconds=None,
                    mfa_seconds=None,
                    started_at=None,
                    finished_at=None,
                )
        else:
            # Kiểm TẤT CẢ combo trước khi ghi job nào. Trước đây parse ngay trong
            # vòng lặp: một dòng hỏng ở giữa làm hàm ném lỗi sau khi đã chèn các
            # job trước đó, để lại job kẹt `queued` mà không worker nào chạy — UI
            # thấy còn job đang chờ nên khoá nút Run vĩnh viễn.
            parsed: list[tuple[str, str]] = []
            for index, combo in enumerate(combos, 1):
                try:
                    email, _ = build_request_from_combo(combo)
                except Exception as exc:
                    raise InvalidComboError(index, str(exc)) from exc
                parsed.append((combo.strip(), email))

            for combo, email in parsed:
                job_id = uuid.uuid4().hex
                fingerprint_seed = new_seed()
                fingerprint_profile = profile_for_seed(fingerprint_seed).name
                profile_identity = generate_profile_identity(
                    profile_region,
                    seed=job_id,
                )
                jobs_repo.create(
                    {
                        "id": job_id,
                        "email": email,
                        "combo": combo.strip(),
                        "mail_mode": "outlook",
                        "reg_mode": reg_mode,
                        "status": "queued",
                        "error": None,
                        "password": None,
                        "session_path": None,
                        "created_at": time.time(),
                        "started_at": None,
                        "finished_at": None,
                        "fingerprint_seed": fingerprint_seed,
                        "fingerprint_profile": fingerprint_profile,
                        "fingerprint_data": None,
                        "profile_region": profile_identity.region,
                        "profile_name": profile_identity.name,
                        "birthdate": profile_identity.birthdate,
                    }
                )
                ids.append(job_id)

        if not ids:
            return []

        pending: queue.Queue[str] = queue.Queue()
        for job_id in ids:
            pending.put(job_id)

        workers = min(workers, len(ids))
        self._emit({"type": "batch", "status": "running", "concurrency": workers, "jobs": len(ids)})
        with self._lock:
            self._active_workers = workers
        for index in range(workers):
            threading.Thread(
                target=self._worker,
                args=(
                    pending,
                    headless,
                    with_2fa,
                    reg_mode,
                    fallback_enabled,
                    jobs_repo,
                    proxy_pool,
                ),
                name=f"reg-worker-{index}",
                daemon=True,
            ).start()
        return ids

    def _worker(
        self,
        pending: "queue.Queue[str]",
        headless: bool,
        with_2fa: bool,
        reg_mode: str,
        fallback_enabled: bool,
        jobs_repo,
        proxy_pool: ProxyPool | None,
    ) -> None:
        try:
            while True:
                try:
                    job_id = pending.get_nowait()
                except queue.Empty:
                    return
                try:
                    if self._stop_all.is_set() or self._should_cancel(job_id):
                        self._finish_job(jobs_repo, job_id, status="cancelled", error="stopped")
                        continue
                    row = jobs_repo.get(job_id)
                    if not row:
                        continue
                    self._run_one(
                        jobs_repo, row, headless=headless, with_2fa=with_2fa,
                        reg_mode=reg_mode, fallback_enabled=fallback_enabled,
                        proxy_pool=proxy_pool,
                    )
                except Exception as exc:
                    # Worker chết lặng lẽ sẽ để job treo ở "running" vĩnh viễn.
                    self._finish_job(
                        jobs_repo, job_id, status="error", error=f"{type(exc).__name__}: {exc}"
                    )
                finally:
                    pending.task_done()
        finally:
            with self._lock:
                self._active_workers -= 1
                last = self._active_workers <= 0
            if last:
                self._emit({"type": "batch", "status": "idle"})

    def start_rental_batch(
        self,
        *,
        rental_count: int,
        provider_factory: Callable[[], MailRentalProvider],
        jobs_repo,
        rentals_repo,
        source: str,
        product_id: str | None,
        alias_limit: int,
        aliases_enabled: bool,
        profile_region: str,
        headless: bool,
        with_2fa: bool = False,
        reg_mode: str = "browser",
        fallback_enabled: bool = False,
        concurrency: int = 1,
        balance_before: int | None = None,
    ) -> list[str]:
        if self.running:
            return []
        if not isinstance(rental_count, int) or rental_count < 1:
            raise ValueError("rental_count must be a positive integer")
        if not isinstance(alias_limit, int) or alias_limit < 1:
            raise ValueError("alias_limit must be a positive integer")
        if type(aliases_enabled) is not bool:
            raise ValueError("aliases_enabled must be a boolean")
        self._stop_all.clear()
        rental_ids = [uuid.uuid4().hex for _ in range(rental_count)]
        pending: queue.Queue[str] = queue.Queue()
        for rental_id in rental_ids:
            pending.put(rental_id)
        workers = min(
            clamp_concurrency(concurrency, reg_mode, fallback_enabled),
            rental_count,
        )
        self._emit(
            {
                "type": "batch",
                "status": "running",
                "concurrency": workers,
                "rentals": rental_count,
                "source": source,
            }
        )
        with self._lock:
            self._active_workers = workers
        for index in range(workers):
            threading.Thread(
                target=self._rental_worker,
                args=(
                    pending,
                    provider_factory,
                    jobs_repo,
                    rentals_repo,
                    source,
                    product_id,
                    alias_limit,
                    aliases_enabled,
                    profile_region,
                    headless,
                    with_2fa,
                    reg_mode,
                    fallback_enabled,
                    balance_before,
                ),
                name=f"rental-worker-{index}",
                daemon=True,
            ).start()
        return rental_ids

    def _rental_worker(
        self,
        pending: "queue.Queue[str]",
        provider_factory: Callable[[], MailRentalProvider],
        jobs_repo,
        rentals_repo,
        source: str,
        product_id: str | None,
        alias_limit: int,
        aliases_enabled: bool,
        profile_region: str,
        headless: bool,
        with_2fa: bool,
        reg_mode: str,
        fallback_enabled: bool,
        balance_before: int | None,
    ) -> None:
        coordinator = RentalCoordinator()
        try:
            while True:
                try:
                    rental_id = pending.get_nowait()
                except queue.Empty:
                    return
                try:
                    if self._stop_all.is_set():
                        continue
                    provider = provider_factory()
                    coordinator.run_rental(
                        rental_id=rental_id,
                        provider=provider,
                        rentals_repo=rentals_repo,
                        jobs_repo=jobs_repo,
                        source=source,
                        product_id=product_id,
                        alias_limit=alias_limit,
                        aliases_enabled=aliases_enabled,
                        profile_region=profile_region,
                        reg_mode=reg_mode,
                        execute=lambda row, mailbox: self._run_one(
                            jobs_repo,
                            row,
                            headless=headless,
                            with_2fa=with_2fa,
                            reg_mode=reg_mode,
                            fallback_enabled=fallback_enabled,
                            mail_override=mailbox,
                        ),
                        should_cancel=self._stop_all.is_set,
                        balance_before=balance_before,
                    )
                except Exception as exc:
                    self._emit(
                        {
                            "type": "rental",
                            "rental_id": rental_id,
                            "status": "error",
                            "error": sanitize_job_log_line(
                                f"{type(exc).__name__}: {exc}"
                            ),
                        }
                    )
                finally:
                    pending.task_done()
        finally:
            with self._lock:
                self._active_workers -= 1
                last = self._active_workers <= 0
            if last:
                self._emit({"type": "batch", "status": "idle"})

    def start_rental_retry_batch(
        self,
        *,
        job_ids: list[str],
        provider_factory: Callable[[str], MailRentalProvider],
        jobs_repo,
        rentals_repo,
        headless: bool,
        with_2fa: bool = False,
        reg_mode: str = "browser",
        fallback_enabled: bool = False,
        concurrency: int = 1,
    ) -> list[str]:
        """Retry Gmail jobs with their existing paid mailbox rental.

        Validation happens before any job is reset. The worker only reconstructs
        the mailbox bridge; it never rents, rerents, or closes the rental.
        """
        if self.running:
            return []
        ids = list(job_ids)
        if not ids:
            return []
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate Gmail retry job id")

        prepared: list[tuple[str, str, dict[str, Any], MailRental]] = []
        for job_id in ids:
            row = jobs_repo.get(job_id)
            if row is None:
                raise ValueError(f"Gmail retry job missing: {job_id}")
            source = str(row.get("mail_mode") or "").strip()
            if source not in ("gmail_smsbower", "gmail_accstack"):
                raise ValueError(f"job is not a Gmail rental: {job_id}")
            rental_id = str(row.get("rental_id") or "").strip()
            if not rental_id:
                raise ValueError(f"Gmail retry rental missing for job: {job_id}")
            rental_row = rentals_repo.get(rental_id)
            if rental_row is None:
                raise ValueError(f"Gmail retry rental missing: {rental_id}")
            if _is_expired(rental_row.get("expires_at")):
                raise ValueError(f"Gmail retry rental expired: {rental_id}")

            source_email = str(row.get("source_email") or "").strip()
            alias = str(row.get("email") or "").strip()
            stored_base_email = str(rental_row.get("base_email") or "").strip()
            external_id = str(rental_row.get("external_id") or "").strip()
            provider_id = str(rental_row.get("provider") or "").strip()
            if not source_email or not alias:
                raise ValueError(f"Gmail retry job has no source email/alias: {job_id}")
            if stored_base_email and stored_base_email.casefold() != source_email.casefold():
                raise ValueError(f"Gmail retry source email does not match rental: {job_id}")
            if not external_id or not provider_id:
                raise ValueError(f"Gmail retry rental is incomplete: {rental_id}")

            rental = MailRental(
                provider=provider_id,
                external_id=external_id,
                base_email=source_email,
                product_id=rental_row.get("product_id"),
                expires_at=rental_row.get("expires_at"),
                balance_after_rent=rental_row.get("balance_after_rent"),
            )
            prepared.append((job_id, source, row, rental))

        self._stop_all.clear()
        for job_id, _source, _row, _rental in prepared:
            jobs_repo.ensure_fingerprint_identity(job_id)
            with self._lock:
                self._cancelled_jobs.discard(job_id)
            jobs_repo.clear_logs(job_id)
            jobs_repo.update(
                job_id,
                reg_mode=reg_mode,
                status="queued",
                error=None,
                session_path=None,
                mfa_activated=0,
                browser_seconds=None,
                http_seconds=None,
                mfa_seconds=None,
                started_at=None,
                finished_at=None,
            )

        pending: queue.Queue[tuple[str, str, dict[str, Any], MailRental]] = queue.Queue()
        for item in prepared:
            pending.put(item)
        workers = min(
            clamp_concurrency(concurrency, reg_mode, fallback_enabled),
            len(prepared),
        )
        self._emit(
            {
                "type": "batch",
                "status": "running",
                "concurrency": workers,
                "jobs": len(prepared),
                "retry": True,
            }
        )
        with self._lock:
            self._active_workers = workers
        for index in range(workers):
            threading.Thread(
                target=self._rental_retry_worker,
                args=(
                    pending,
                    provider_factory,
                    jobs_repo,
                    headless,
                    with_2fa,
                    reg_mode,
                    fallback_enabled,
                ),
                name=f"rental-retry-worker-{index}",
                daemon=True,
            ).start()
        return ids

    def _rental_retry_worker(
        self,
        pending: "queue.Queue[tuple[str, str, dict[str, Any], MailRental]]",
        provider_factory: Callable[[str], MailRentalProvider],
        jobs_repo,
        headless: bool,
        with_2fa: bool,
        reg_mode: str,
        fallback_enabled: bool,
    ) -> None:
        try:
            while True:
                try:
                    job_id, source, row, rental = pending.get_nowait()
                except queue.Empty:
                    return
                try:
                    if self._stop_all.is_set() or self._should_cancel(job_id):
                        self._finish_job(
                            jobs_repo,
                            job_id,
                            status="cancelled",
                            error="stopped",
                        )
                        continue
                    provider = provider_factory(source)
                    if str(provider.provider_id) != rental.provider:
                        raise ValueError(
                            f"Gmail retry provider mismatch: "
                            f"{provider.provider_id!r} != {rental.provider!r}"
                        )
                    mailbox = RentalMailboxProvider(
                        provider,
                        rental,
                        str(row["email"]),
                        lambda current_job_id=job_id: self._should_cancel(current_job_id),
                    )
                    self._run_one(
                        jobs_repo,
                        row,
                        headless=headless,
                        with_2fa=with_2fa,
                        reg_mode=reg_mode,
                        fallback_enabled=fallback_enabled,
                        mail_override=mailbox,
                    )
                except Exception as exc:
                    self._finish_job(
                        jobs_repo,
                        job_id,
                        status="error",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                finally:
                    pending.task_done()
        finally:
            with self._lock:
                self._active_workers -= 1
                last = self._active_workers <= 0
            if last:
                self._emit({"type": "batch", "status": "idle"})

    def _attempt_signup(
        self, jobs_repo, row: dict[str, Any], job_id: str, *,
        headless: bool, with_2fa: bool, reg_mode: str, log,
        mail_override=None,
        proxy_url: str | None = None,
        proxy_enabled: bool | None = None,
    ):
        identity = jobs_repo.ensure_fingerprint_identity(job_id)
        fingerprint_seed = str(identity["fingerprint_seed"])
        fingerprint_profile = str(identity["fingerprint_profile"])
        profile = get_profile(fingerprint_profile)
        browser_fingerprint = None
        if reg_mode == "browser":
            stored = identity.get("fingerprint_data")
            if stored is None:
                materialized = materialize_browser_fingerprint(fingerprint_seed)
                stored = jobs_repo.set_fingerprint_data_if_empty(
                    job_id,
                    serialize_browser_fingerprint(materialized),
                )
            browser_fingerprint = parse_browser_fingerprint(
                stored,
                expected_seed=fingerprint_seed,
            )

        log(
            f"[fingerprint] identity={identity_id(fingerprint_seed)} "
            f"engine={reg_mode} profile={profile.name}"
        )
        # Retry: mật khẩu đã ghi ở lần chạy trước là mật khẩu **tài khoản
        # ChatGPT** (do tool sinh, đã ghi ngay khi register 200). Mật khẩu trong
        # combo là của **hộp thư Hotmail** — nộp nó vào password/verify thì 401
        # invalid_username_or_password và mất luôn cơ hội cứu acc.
        saved_password = str(row.get("password") or "").strip() or None
        if saved_password:
            log("[job] dùng mật khẩu tài khoản đã ghi ở lần chạy trước")
        if mail_override is None:
            email, password = build_request_from_combo(
                row["combo"], password_override=saved_password
            )
            outlook_combo = row["combo"]
            mail_provider = "outlook"
        else:
            email = str(row["email"])
            password = saved_password
            outlook_combo = None
            mail_provider = str(row.get("mail_mode") or "gmail_smsbower")
        if mail_override is not None and proxy_enabled is None:
            proxy_url = getattr(mail_override, "proxy_url", None)
            proxy_enabled = bool(proxy_url)
        req = SignupRequest(
            email=email,
            name=str(row.get("profile_name") or "ChatGPT User"),
            birthdate=str(row.get("birthdate") or "2000-01-01"),
            password=password,
            outlook_combo=outlook_combo,
            headless=headless,
            mail_provider=mail_provider,
            reg_mode=reg_mode,
            fingerprint_seed=fingerprint_seed,
            fingerprint_profile=profile.name,
            browser_fingerprint=browser_fingerprint,
            user_agent=profile.user_agent,
            impersonate=profile.impersonate,
            proxy=proxy_url,
            proxy_enabled=proxy_enabled,
        )

        def on_account_created(created_password: str) -> None:
            # Ghi ngay khi OpenAI chấp nhận đăng ký. Trước đây mật khẩu chỉ được
            # lưu lúc job thành công — flow hỏng sau bước này là account tồn tại
            # mà không ai biết mật khẩu.
            jobs_repo.update(job_id, password=created_password, registered_at=time.time())
            log("[job] đã ghi mật khẩu — account tồn tại trên server từ đây")

        return run_signup(
            req,
            log=log,
            with_2fa=with_2fa,
            should_cancel=lambda: self._should_cancel(job_id),
            on_account_created=on_account_created,
            mail=mail_override,
        )

    def _run_one(
        self, jobs_repo, row: dict[str, Any], *, headless: bool, with_2fa: bool,
        reg_mode: str, fallback_enabled: bool = False,
        mail_override=None,
        proxy_pool: ProxyPool | None = None,
    ):
        job_id = str(row["id"])
        with self._lock:
            self._running_jobs.add(job_id)
        jobs_repo.update(job_id, status="running", started_at=time.time())
        self._emit({"type": "job", "job_id": job_id, "status": "running"})

        def log(line: str) -> None:
            public_line = sanitize_job_log_line(line)
            jobs_repo.append_log(job_id, public_line)
            self._emit({"type": "log", "job_id": job_id, "line": public_line})

        proxy_decided = mail_override is not None or proxy_pool is not None
        proxy_url = getattr(mail_override, "proxy_url", None)
        if mail_override is None and proxy_pool is not None:
            proxy_url = proxy_pool.acquire_url()

        def attempt(attempt_row: dict[str, Any], mode: str):
            kwargs = {
                "headless": headless,
                "with_2fa": with_2fa,
                "reg_mode": mode,
                "log": log,
            }
            if mail_override is not None:
                kwargs["mail_override"] = mail_override
            if proxy_decided:
                kwargs["proxy_url"] = proxy_url
                kwargs["proxy_enabled"] = bool(proxy_url)
            return self._attempt_signup(jobs_repo, attempt_row, job_id, **kwargs)

        try:
            log(f"[job] primary={reg_mode}, fallback={'on' if fallback_enabled else 'off'}")
            result = attempt(row, reg_mode)
            fallback_mode = "browser" if reg_mode == "http" else "http"
            if (
                fallback_enabled
                and not result.ok
                and result.error != "cancelled"
                and bool(getattr(result, "fallback_eligible", False))
                and not self._should_cancel(job_id)
            ):
                primary_error = result.error or "unknown error"
                primary_browser_seconds = result.browser_seconds
                primary_http_seconds = result.http_seconds
                fresh = jobs_repo.get(job_id) or row
                log(
                    f"[job] primary={reg_mode} thất bại ({primary_error}); "
                    f"fallback={fallback_mode}"
                )
                self._emit({"type": "job", "job_id": job_id, "status": "running"})
                fallback_result = attempt(fresh, fallback_mode)
                if fallback_result.browser_seconds is None:
                    fallback_result.browser_seconds = primary_browser_seconds
                if fallback_result.http_seconds is None:
                    fallback_result.http_seconds = primary_http_seconds
                if not fallback_result.ok and fallback_result.error != "cancelled":
                    fallback_result.error = (
                        f"primary {reg_mode}: {primary_error}; "
                        f"fallback {fallback_mode}: {fallback_result.error or 'unknown error'}"
                    )
                result = fallback_result
        finally:
            with self._lock:
                self._running_jobs.discard(job_id)

        if mail_override is not None and result.outcome == "account_exists":
            self._finish_job(
                jobs_repo,
                job_id,
                status="error",
                error=result.error or "account already exists",
                browser_seconds=result.browser_seconds,
                http_seconds=result.http_seconds,
            )
        elif result.ok:
            self._finish_job(
                jobs_repo,
                job_id,
                status="success",
                password=result.password,
                session_path=result.session_path,
                mfa_activated=1 if result.mfa_activated else 0,
                browser_seconds=result.browser_seconds,
                http_seconds=result.http_seconds,
                mfa_seconds=result.mfa_seconds,
            )
        elif result.error == "cancelled":
            self._finish_job(jobs_repo, job_id, status="cancelled", error="stopped")
        else:
            self._finish_job(
                jobs_repo,
                job_id,
                status="error",
                error=result.error,
                browser_seconds=result.browser_seconds,
                http_seconds=result.http_seconds,
            )
        return result

    def _finish_job(self, jobs_repo, job_id: str, *, status: str, **fields: Any) -> None:
        if fields.get("error") is not None:
            fields["error"] = sanitize_job_log_line(str(fields["error"]))
        jobs_repo.update(job_id, status=status, finished_at=time.time(), **fields)
        with self._lock:
            self._cancelled_jobs.discard(job_id)
            self._running_jobs.discard(job_id)
        public_fields = {
            key: fields[key]
            for key in (
                "error",
                "mfa_activated",
                "browser_seconds",
                "http_seconds",
                "mfa_seconds",
            )
            if key in fields
        }
        self._emit({"type": "job", "job_id": job_id, "status": status, **public_fields})
