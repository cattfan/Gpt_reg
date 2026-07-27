"""Kiem tra migration va compare-and-set fingerprint trong SQLite."""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from gpt_reg.fingerprint import device_id_for_seed, identity_id, profile_for_seed


def _legacy_db() -> Path:
    path = Path(tempfile.mkdtemp()) / "legacy-v4.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE _schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now')),
            description TEXT
        );
        INSERT INTO _schema_version(version, description) VALUES (4, 'legacy v4');
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            combo TEXT NOT NULL,
            mail_mode TEXT NOT NULL DEFAULT 'outlook',
            reg_mode TEXT NOT NULL DEFAULT 'browser',
            status TEXT NOT NULL DEFAULT 'queued',
            error TEXT,
            password TEXT,
            session_path TEXT,
            created_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL,
            mfa_activated INTEGER NOT NULL DEFAULT 0,
            browser_seconds REAL,
            http_seconds REAL,
            mfa_seconds REAL,
            registered_at REAL
        );
        CREATE TABLE settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            value TEXT,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE TABLE job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            line TEXT NOT NULL,
            created_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
        );
        CREATE TABLE checks (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            combo TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            plan TEXT,
            plan_detail TEXT,
            has_subscription INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT,
            mfa_enabled INTEGER NOT NULL DEFAULT 0,
            deactivated INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            seconds REAL,
            created_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL
        );
        INSERT INTO jobs (
            id, email, combo, mail_mode, reg_mode, status, created_at
        ) VALUES ('legacy-job', 'legacy@example.com', 'combo', 'outlook', 'http', 'error', 1.0);
        """
    )
    conn.commit()
    conn.close()
    return path


def _insert_job(repo, job_id: str) -> None:
    repo.create(
        {
            "id": job_id,
            "email": f"{job_id}@example.com",
            "combo": "combo",
            "mail_mode": "outlook",
            "reg_mode": "http",
            "status": "error",
            "created_at": time.time(),
        }
    )


def _check_batch_identity(repo, failures: list[str]) -> None:
    from gpt_reg.web.jobs.reg_manager import RegJobManager

    client_id = "12345678-1234-1234-1234-123456789abc"
    combos = [
        f"batch{i}@hotmail.com|Passw0rd{i}|refresh-{i}|{client_id}"
        for i in range(200)
    ]
    manager = RegJobManager()
    manager._worker = lambda *_args, **_kwargs: None
    ids = manager.start_batch(
        combos=combos,
        headless=True,
        jobs_repo=repo,
        reg_mode="http",
        concurrency=1,
    )
    if len(ids) != 200:
        failures.append(f"batch tao {len(ids)} job, can 200")
        return

    rows = [repo.get(job_id) for job_id in ids]
    seeds = [row.get("fingerprint_seed") for row in rows if row]
    profiles = [row.get("fingerprint_profile") for row in rows if row]
    if len(seeds) != 200 or any(not seed for seed in seeds):
        failures.append("job moi chua co fingerprint_seed")
        return
    if len(set(seeds)) != 200:
        failures.append("batch 200 job bi trung fingerprint_seed")
    if len({identity_id(seed) for seed in seeds}) != 200:
        failures.append("batch 200 job bi trung identity_id")
    if len({device_id_for_seed(seed, "http") for seed in seeds}) != 200:
        failures.append("batch 200 job bi trung HTTP device ID")
    for seed, profile in zip(seeds, profiles):
        if not profile or profile_for_seed(seed).name != profile:
            failures.append("job moi co seed/profile khong tuong quan")
            break
    if any(row.get("fingerprint_data") for row in rows if row):
        failures.append("batch HTTP da materialize Browser fingerprint")

    retry_id = ids[0]
    repo.update(retry_id, status="error")
    repo.set_fingerprint_data_if_empty(retry_id, '{"stored":true}')
    before = repo.ensure_fingerprint_identity(retry_id)
    retry_manager = RegJobManager()
    retry_manager._worker = lambda *_args, **_kwargs: None
    retry_manager.start_batch(
        combos=[],
        headless=True,
        jobs_repo=repo,
        reg_mode="browser",
        concurrency=1,
        job_ids=[retry_id],
    )
    after = repo.ensure_fingerprint_identity(retry_id)
    if after != before:
        failures.append("retry da doi seed/profile/data")


def main() -> int:
    from gpt_reg.db import connect, migrate
    from gpt_reg.db import schema
    from gpt_reg.db.repositories import JobRepository

    failures: list[str] = []
    path = _legacy_db()
    conn = connect(path)
    version = migrate(conn)
    repo = JobRepository(conn)

    if version != schema.CURRENT_VERSION:
        failures.append(
            f"migration version={version}, can {schema.CURRENT_VERSION}"
        )
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(jobs)")}
    expected_columns = {"fingerprint_seed", "fingerprint_profile", "fingerprint_data"}
    if not expected_columns.issubset(columns):
        failures.append(f"migration thieu cot: {sorted(expected_columns - columns)}")

    first = repo.ensure_fingerprint_identity("legacy-job")
    second = repo.ensure_fingerprint_identity("legacy-job")
    if first != second:
        failures.append("legacy backfill thay doi qua lan goi thu hai")
    if profile_for_seed(first["fingerprint_seed"]).name != first["fingerprint_profile"]:
        failures.append("legacy backfill seed/profile khong tuong quan")

    _insert_job(repo, "partial-job")
    repo.update("partial-job", fingerprint_seed="01" * 16)
    try:
        repo.ensure_fingerprint_identity("partial-job")
    except ValueError:
        pass
    else:
        failures.append("identity chi co seed khong fail-fast")

    _insert_job(repo, "mismatched-job")
    mismatched_seed = "02" * 16
    expected_profile = profile_for_seed(mismatched_seed).name
    mismatched_profile = next(
        profile_for_seed(f"{value:032x}").name
        for value in range(1, 1000)
        if profile_for_seed(f"{value:032x}").name != expected_profile
    )
    repo.update(
        "mismatched-job",
        fingerprint_seed=mismatched_seed,
        fingerprint_profile=mismatched_profile,
    )
    try:
        repo.ensure_fingerprint_identity("mismatched-job")
    except ValueError:
        pass
    else:
        failures.append("identity co seed/profile lech nhau khong fail-fast")

    _insert_job(repo, "race-job")
    barrier = threading.Barrier(20)
    identities: list[tuple[str, str]] = []
    identity_errors: list[str] = []
    guard = threading.Lock()

    def claim_identity(index: int) -> None:
        seed = f"{index + 1000:032x}"
        profile = profile_for_seed(seed).name
        try:
            barrier.wait(timeout=5)
            value = repo.ensure_fingerprint_identity(
                "race-job", proposed_seed=seed, proposed_profile=profile
            )
            with guard:
                identities.append((value["fingerprint_seed"], value["fingerprint_profile"]))
        except Exception as exc:
            with guard:
                identity_errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=claim_identity, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    if identity_errors or len(identities) != 20 or len(set(identities)) != 1:
        failures.append(
            f"identity CAS race loi={identity_errors}, count={len(identities)}, "
            f"unique={len(set(identities))}"
        )

    data_barrier = threading.Barrier(20)
    stored_data: list[str] = []
    data_errors: list[str] = []

    def claim_data(index: int) -> None:
        payload = f'{{"version":1,"winner":{index}}}'
        try:
            data_barrier.wait(timeout=5)
            value = repo.set_fingerprint_data_if_empty("race-job", payload)
            with guard:
                stored_data.append(value)
        except Exception as exc:
            with guard:
                data_errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=claim_data, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    if data_errors or len(stored_data) != 20 or len(set(stored_data)) != 1:
        failures.append(
            f"fingerprint_data CAS race loi={data_errors}, count={len(stored_data)}, "
            f"unique={len(set(stored_data))}"
        )

    _check_batch_identity(repo, failures)

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] fingerprint storage" if failures else "[ok] fingerprint storage")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
