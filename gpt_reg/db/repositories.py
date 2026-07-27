from __future__ import annotations

import hashlib
import sqlite3
import threading
from typing import Any

_EXACT_KEYS: frozenset[str] = frozenset(
    {
        "proxy.pool",
        "proxy.enabled",
        "reg.headless",
        "reg.password",
        "reg.source",
        "mail_mode.provider",
        "browser.geoip",
        "ui.theme",
        "web.port",
        "accstack.api_key",
        "mail.smsbower.alias_limit",
        "mail.accstack.alias_limit",
        "sms.smsbower.api_key",
        "sms.smsbower.country",
    }
)


# Không bao giờ trả nguyên văn qua API — `all_known()` che lại.
_SECRET_KEYS: frozenset[str] = frozenset({"accstack.api_key", "sms.smsbower.api_key"})

# Giá trị che mà UI gửi ngược lên khi lưu form: bỏ qua, giữ giá trị cũ.
MASKED_VALUE = "•" * 8


def _validate_type(key: str, value: str | None) -> None:
    if key in ("reg.headless", "browser.geoip", "proxy.enabled") and value is not None:
        if value not in ("0", "1", "true", "false", "yes", "no", "on", "off"):
            raise ValueError(f"{key} must be bool-like, got {value!r}")
    if key == "web.port" and value is not None:
        if not str(value).isdigit():
            raise ValueError(f"web.port must be int, got {value!r}")


class SettingsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, key: str, default: str | None = None) -> str | None:
        if key not in _EXACT_KEYS:
            raise KeyError(f"unknown settings key: {key}")
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None or row["value"] is None:
            return default
        return str(row["value"])

    def set(self, key: str, value: str | None) -> None:
        if key not in _EXACT_KEYS:
            raise KeyError(f"unknown settings key: {key}")
        _validate_type(key, value)
        self._conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            """,
            (key, value),
        )
        self._conn.commit()

    def apply_defaults(self, defaults: dict[str, str | None]) -> None:
        for key, val in defaults.items():
            if self.get(key) is None and val is not None:
                self.set(key, val)

    def all_known(self) -> dict[str, str | None]:
        """Mọi setting, nhưng che secret — endpoint này chảy thẳng ra browser."""
        out: dict[str, str | None] = {}
        for key in sorted(_EXACT_KEYS):
            value = self.get(key)
            if key in _SECRET_KEYS and value:
                out[key] = MASKED_VALUE
            else:
                out[key] = value
        return out

    def has_value(self, key: str) -> bool:
        return bool(self.get(key))


class MailRentalRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._write_lock = threading.Lock()

    def create(self, row: dict[str, Any]) -> None:
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        with self._write_lock, self._conn:
            self._conn.execute(
                f"INSERT INTO mail_rentals ({cols}) VALUES ({placeholders})",
                tuple(row.values()),
            )

    def get(self, rental_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM mail_rentals WHERE id = ?", (rental_id,)
        ).fetchone()
        return dict(row) if row else None

    def update(self, rental_id: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{key} = ?" for key in fields)
        with self._write_lock, self._conn:
            self._conn.execute(
                f"UPDATE mail_rentals SET {sets} WHERE id = ?",
                (*fields.values(), rental_id),
            )

    def list_recent(self, limit: int | None = 500) -> list[dict[str, Any]]:
        if limit is None:
            rows = self._conn.execute(
                "SELECT * FROM mail_rentals ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM mail_rentals ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]


class ProxyRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._write_lock = threading.Lock()

    def list_all(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM proxies ORDER BY id").fetchall()
        result = [dict(row) for row in rows]
        for row in result:
            row["selected"] = bool(row["selected"])
        return result

    def replace_all(self, rows: list[dict[str, Any]]) -> None:
        from gpt_reg.proxy.format import materialize_proxy

        normalized: list[tuple[str, bool]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("proxy row must be an object")
            value = row.get("value")
            if not isinstance(value, str):
                raise ValueError("proxy value must be a string")
            value = value.strip()
            materialize_proxy(value)
            if value in seen:
                raise ValueError(f"duplicate proxy value: {value!r}")
            selected = row.get("selected", True)
            if not isinstance(selected, bool):
                raise ValueError("proxy selected must be bool")
            seen.add(value)
            normalized.append((value, selected))

        with self._write_lock, self._conn:
            if normalized:
                marks = ", ".join("?" for _ in normalized)
                self._conn.execute(
                    f"DELETE FROM proxies WHERE value NOT IN ({marks})",
                    tuple(value for value, _selected in normalized),
                )
            else:
                self._conn.execute("DELETE FROM proxies")
            for value, selected in normalized:
                self._conn.execute(
                    """
                    INSERT INTO proxies (value, selected) VALUES (?, ?)
                    ON CONFLICT(value) DO UPDATE SET
                        selected = excluded.selected,
                        updated_at = unixepoch('subsec')
                    """,
                    (value, int(selected)),
                )


class JobRepository:
    """Kho job dùng chung cho nhiều worker thread.

    SQLite ở chế độ WAL cho phép nhiều reader + **một** writer. Chạy 200 luồng
    mà ghi tự do sẽ dính `database is locked`, nên mọi thao tác ghi đi qua một
    lock. Đọc không cần lock.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._write_lock = threading.Lock()

    def create(self, job: dict[str, Any]) -> None:
        cols = ", ".join(job.keys())
        placeholders = ", ".join("?" for _ in job)
        with self._write_lock:
            self._conn.execute(
                f"INSERT INTO jobs ({cols}) VALUES ({placeholders})",
                tuple(job.values()),
            )
            self._conn.commit()

    def update(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._write_lock:
            self._conn.execute(
                f"UPDATE jobs SET {sets} WHERE id = ?",
                (*fields.values(), job_id),
            )
            self._conn.commit()

    def list_recent(self, limit: int | None = 500) -> list[dict[str, Any]]:
        """Job mới nhất. `limit=None` để lấy hết (dùng khi xuất kết quả).

        Mặc định 500 chứ không phải 50: batch tối đa là 200 job, giới hạn 50 sẽ
        giấu mất phần lớn batch khỏi UI và khỏi bản xuất `status=all`.
        """
        if limit is None:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_all(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()
        return int(row["n"]) if row else 0

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def ensure_fingerprint_identity(
        self,
        job_id: str,
        *,
        proposed_seed: str | None = None,
        proposed_profile: str | None = None,
    ) -> dict[str, str | None]:
        """Return one stable seed/profile pair, creating it exactly once when absent."""
        from gpt_reg.fingerprint import get_profile, profile_for_seed, validate_seed

        if (proposed_seed is None) != (proposed_profile is None):
            raise ValueError("proposed fingerprint seed/profile must be supplied together")

        with self._write_lock:
            row = self._conn.execute(
                "SELECT fingerprint_seed, fingerprint_profile, fingerprint_data "
                "FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"job not found: {job_id}")

            current_seed = row["fingerprint_seed"]
            current_profile = row["fingerprint_profile"]
            if (current_seed is None) != (current_profile is None):
                raise ValueError(f"corrupt fingerprint identity for job {job_id}")

            if current_seed is None:
                if proposed_seed is None:
                    proposed_seed = hashlib.sha256(
                        f"gpt-reg:legacy-fingerprint:v1:{job_id}".encode("utf-8")
                    ).hexdigest()[:32]
                    proposed_profile = profile_for_seed(proposed_seed).name
                else:
                    proposed_seed = validate_seed(proposed_seed)
                    proposed_profile = get_profile(proposed_profile).name
                    if proposed_profile != profile_for_seed(proposed_seed).name:
                        raise ValueError("proposed fingerprint profile does not match seed")

                self._conn.execute(
                    "UPDATE jobs SET fingerprint_seed = ?, fingerprint_profile = ? "
                    "WHERE id = ? AND fingerprint_seed IS NULL AND fingerprint_profile IS NULL",
                    (proposed_seed, proposed_profile, job_id),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT fingerprint_seed, fingerprint_profile, fingerprint_data "
                    "FROM jobs WHERE id = ?",
                    (job_id,),
                ).fetchone()

            seed = validate_seed(str(row["fingerprint_seed"]))
            profile = get_profile(str(row["fingerprint_profile"])).name
            expected_profile = profile_for_seed(seed).name
            if profile != expected_profile:
                raise ValueError(
                    f"corrupt fingerprint identity for job {job_id}: "
                    "profile does not match seed"
                )
            return {
                "fingerprint_seed": seed,
                "fingerprint_profile": profile,
                "fingerprint_data": (
                    None if row["fingerprint_data"] is None else str(row["fingerprint_data"])
                ),
            }

    def set_fingerprint_data_if_empty(self, job_id: str, payload: str) -> str:
        """Store the first materialized Browser fingerprint and return the winner."""
        if not isinstance(payload, str) or not payload.strip():
            raise ValueError("fingerprint data must be a nonempty JSON string")
        with self._write_lock:
            self._conn.execute(
                "UPDATE jobs SET fingerprint_data = ? "
                "WHERE id = ? AND fingerprint_data IS NULL",
                (payload, job_id),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT fingerprint_data FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"job not found: {job_id}")
            value = row["fingerprint_data"]
            if value is None:
                raise RuntimeError(f"fingerprint data CAS failed for job {job_id}")
            return str(value)

    def append_log(self, job_id: str, line: str) -> None:
        # Đường ghi nóng nhất: mọi dòng log của mọi job đều đi qua đây.
        import time

        with self._write_lock:
            self._conn.execute(
                "INSERT INTO job_logs (job_id, line, created_at) VALUES (?, ?, ?)",
                (job_id, line, time.time()),
            )
            self._conn.commit()

    def logs(self, job_id: str, limit: int = 200) -> list[str]:
        rows = self._conn.execute(
            "SELECT line FROM job_logs WHERE job_id = ? ORDER BY id DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()
        return [str(r["line"]) for r in reversed(rows)]

    def list_by_status(self, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
        if not statuses:
            return []
        marks = ", ".join("?" for _ in statuses)
        rows = self._conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY created_at",
            statuses,
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_by_status(self, statuses: tuple[str, ...]) -> int:
        """Xoá job theo trạng thái. `job_logs` đi theo nhờ ON DELETE CASCADE.

        Không bao giờ xoá job đang chạy — caller chỉ truyền trạng thái kết thúc.
        """
        if not statuses:
            return 0
        marks = ", ".join("?" for _ in statuses)
        with self._write_lock:
            cur = self._conn.execute(f"DELETE FROM jobs WHERE status IN ({marks})", statuses)
            self._conn.commit()
        return cur.rowcount or 0

    def delete_ids(
        self,
        job_ids: tuple[str, ...],
        *,
        allowed_statuses: tuple[str, ...],
    ) -> int:
        """Xoa cac job terminal duoc chon; queued/running luon duoc bao ve."""
        ids = tuple(dict.fromkeys(str(job_id) for job_id in job_ids if str(job_id)))
        terminal = frozenset(("success", "error", "cancelled"))
        statuses = tuple(dict.fromkeys(s for s in allowed_statuses if s in terminal))
        if not ids or not statuses:
            return 0

        id_marks = ", ".join("?" for _ in ids)
        status_marks = ", ".join("?" for _ in statuses)
        with self._write_lock:
            cur = self._conn.execute(
                f"DELETE FROM jobs WHERE id IN ({id_marks}) AND status IN ({status_marks})",
                (*ids, *statuses),
            )
            self._conn.commit()
        return cur.rowcount or 0

    def reap_orphans(self) -> int:
        """Đánh dấu job còn `running`/`queued` từ process trước là đã huỷ.

        Worker sống trong process; server tắt là chúng chết theo nhưng hàng trong
        DB vẫn `running` vĩnh viễn — khoá luôn nút Run vì UI tưởng đang bận.
        """
        import time

        with self._write_lock:
            cur = self._conn.execute(
                "UPDATE jobs SET status = 'cancelled', error = 'interrupted', finished_at = ? "
                "WHERE status IN ('running', 'queued')",
                (time.time(),),
            )
            self._conn.commit()
        return cur.rowcount or 0

    def clear_logs(self, job_id: str) -> None:
        with self._write_lock:
            self._conn.execute("DELETE FROM job_logs WHERE job_id = ?", (job_id,))
            self._conn.commit()


class ChecksRepository:
    """Kho kết quả check plan — cùng khuôn ghi-qua-lock như JobRepository.

    Tách khỏi `jobs` vì vòng đời khác: không đăng ký, không session file, chạy
    nhanh và số lượng lớn. Cùng một `conn` (WAL, một writer) nên vẫn dùng lock
    ghi riêng của bảng này.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._write_lock = threading.Lock()

    def create(self, row: dict[str, Any]) -> None:
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        with self._write_lock:
            self._conn.execute(
                f"INSERT INTO checks ({cols}) VALUES ({placeholders})", tuple(row.values())
            )
            self._conn.commit()

    def update(self, check_id: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._write_lock:
            self._conn.execute(
                f"UPDATE checks SET {sets} WHERE id = ?", (*fields.values(), check_id)
            )
            self._conn.commit()

    def get(self, check_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()
        return dict(row) if row else None

    def append_log(self, check_id: str, line: str) -> None:
        import time

        with self._write_lock, self._conn:
            self._conn.execute(
                "INSERT INTO check_logs (check_id, line, created_at) VALUES (?, ?, ?)",
                (check_id, line, time.time()),
            )

    def logs(self, check_id: str, limit: int = 200) -> list[str]:
        bounded_limit = max(0, min(limit, 500))
        rows = self._conn.execute(
            "SELECT line FROM check_logs WHERE check_id = ? ORDER BY id DESC LIMIT ?",
            (check_id, bounded_limit),
        ).fetchall()
        return [str(row["line"]) for row in reversed(rows)]

    def clear_logs(self, check_id: str) -> None:
        with self._write_lock, self._conn:
            self._conn.execute("DELETE FROM check_logs WHERE check_id = ?", (check_id,))

    def list_recent(self, limit: int | None = 500) -> list[dict[str, Any]]:
        if limit is None:
            rows = self._conn.execute("SELECT * FROM checks ORDER BY created_at DESC").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM checks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def list_by_status(self, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
        if not statuses:
            return []
        marks = ", ".join("?" for _ in statuses)
        rows = self._conn.execute(
            f"SELECT * FROM checks WHERE status IN ({marks}) ORDER BY created_at", statuses
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_by_status(self, statuses: tuple[str, ...]) -> int:
        if not statuses:
            return 0
        marks = ", ".join("?" for _ in statuses)
        with self._write_lock:
            cur = self._conn.execute(f"DELETE FROM checks WHERE status IN ({marks})", statuses)
            self._conn.commit()
        return cur.rowcount or 0

    def reap_orphans(self) -> int:
        """Check còn `running`/`queued` từ process trước → đánh dấu huỷ."""
        import time

        with self._write_lock:
            cur = self._conn.execute(
                "UPDATE checks SET status = 'cancelled', error = 'interrupted', finished_at = ? "
                "WHERE status IN ('running', 'queued')",
                (time.time(),),
            )
            self._conn.commit()
        return cur.rowcount or 0
