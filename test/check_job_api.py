"""Kiểm tra vòng đời job qua HTTP API — không cần chạy signup thật.

`run_signup` bị thay bằng bản giả để test được start/stop/retry/clear/logs mà
không đụng browser hay mạng.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

CLIENT_ID = "12345678-1234-1234-1234-123456789abc"
COMBO = f"a@hotmail.com|Passw0rd123|refresh-token|{CLIENT_ID}"


def _fresh_repo():
    from gpt_reg.db import connect, migrate
    from gpt_reg.db.repositories import JobRepository

    tmp = Path(tempfile.mkdtemp()) / "test.db"
    conn = connect(tmp)
    migrate(conn)
    return JobRepository(conn)


def _wait(predicate, timeout_s: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _check_cancel(failures: list[str]) -> None:
    """Job đang chạy phải dừng khi bật cờ, và ghi trạng thái cancelled."""
    from gpt_reg.models import SignupResult
    from gpt_reg.web.jobs import reg_manager as mod

    repo = _fresh_repo()
    manager = mod.RegJobManager()
    started = {"flag": False}

    def fake_run_signup(request, *, log=None, with_2fa=False, should_cancel=None, **_kw):
        started["flag"] = True
        for _ in range(400):
            if should_cancel and should_cancel():
                return SignupResult(ok=False, email=request.email, error="cancelled", exit_code=3)
            time.sleep(0.02)
        return SignupResult(ok=True, email=request.email, exit_code=0)

    original = mod.run_signup
    mod.run_signup = fake_run_signup
    try:
        ids = manager.start_batch(
            combos=[COMBO], headless=True, jobs_repo=repo, ctx=object(), with_2fa=False
        )
        if not _wait(lambda: started["flag"]):
            failures.append("job không bắt đầu chạy")
            return
        manager.stop_job(ids[0])
        if not _wait(lambda: repo.get(ids[0])["status"] == "cancelled"):
            failures.append(f"stop_job: status={repo.get(ids[0])['status']} (muốn cancelled)")
        if manager.running and not _wait(lambda: not manager.running):
            failures.append("manager vẫn báo running sau khi job dừng")
    finally:
        mod.run_signup = original


def _check_retry_and_clear(failures: list[str]) -> None:
    """Retry giữ nguyên job id; clear không đụng job chưa kết thúc."""
    from gpt_reg.web.jobs import reg_manager as mod

    repo = _fresh_repo()
    now = time.time()
    rows = [
        ("j-ok", "success"),
        ("j-err", "error"),
        ("j-cancel", "cancelled"),
        ("j-run", "running"),
    ]
    for job_id, status in rows:
        repo.create(
            {
                "id": job_id,
                "email": f"{job_id}@x.com",
                "combo": COMBO,
                "mail_mode": "outlook",
                "reg_mode": "browser",
                "status": status,
                "error": None,
                "password": None,
                "session_path": None,
                "created_at": now,
                "started_at": None,
                "finished_at": None,
            }
        )

    retryable = {r["id"] for r in repo.list_by_status(("error", "cancelled"))}
    if retryable != {"j-err", "j-cancel"}:
        failures.append(f"list_by_status trả {retryable}")

    repo.append_log("j-err", "dòng cũ")
    manager = mod.RegJobManager()
    from gpt_reg.models import SignupResult

    original = mod.run_signup
    mod.run_signup = lambda request, **_kwargs: SignupResult(
        ok=False,
        email=request.email,
        error="test retry failure",
    )
    try:
        ids = manager.start_batch(
            combos=[], headless=True, jobs_repo=repo, ctx=object(), job_ids=["j-err"]
        )
        _wait(lambda: not manager.running)
    finally:
        mod.run_signup = original
    if ids != ["j-err"]:
        failures.append(f"retry đổi job id: {ids}")
    if "dòng cũ" in repo.logs("j-err"):
        failures.append("retry không xoá log cũ")

    removed = repo.delete_by_status(("success", "cancelled"))
    if removed != 2:
        failures.append(f"clear done xoá {removed} job (muốn 2)")
    remaining = {r["id"] for r in repo.list_recent()}
    if "j-run" not in remaining:
        failures.append("clear đã xoá nhầm job đang chạy")


def _check_delete_selected_ids(failures: list[str]) -> None:
    """Xoa dung terminal job duoc chon, khong dung running hay job ngoai danh sach."""
    repo = _fresh_repo()
    now = time.time()
    for job_id, status in (
        ("selected-done", "success"),
        ("selected-running", "running"),
        ("unselected-done", "error"),
    ):
        repo.create(
            {
                "id": job_id,
                "email": f"{job_id}@x.com",
                "combo": COMBO,
                "mail_mode": "outlook",
                "reg_mode": "browser",
                "status": status,
                "error": None,
                "password": None,
                "session_path": None,
                "created_at": now,
                "started_at": None,
                "finished_at": None,
            }
        )

    repo.append_log("selected-done", "terminal log")
    removed = repo.delete_ids(
        ("selected-done", "selected-running"),
        allowed_statuses=("success", "error", "cancelled", "running"),
    )
    if removed != 1:
        failures.append(f"delete_ids xoa {removed} job (muon 1)")
    if repo.get("selected-done") is not None:
        failures.append("delete_ids khong xoa terminal job duoc chon")
    if repo.logs("selected-done"):
        failures.append("delete_ids khong cascade job logs")
    if repo.get("selected-running") is None:
        failures.append("delete_ids xoa nham running job")
    if repo.get("unselected-done") is None:
        failures.append("delete_ids xoa nham terminal job khong duoc chon")


def _check_reap_orphans(failures: list[str]) -> None:
    """Job kẹt running/queued từ process trước phải được dọn."""
    repo = _fresh_repo()
    now = time.time()
    for job_id, status in (("o-run", "running"), ("o-queue", "queued"), ("o-ok", "success")):
        repo.create(
            {
                "id": job_id,
                "email": f"{job_id}@x.com",
                "combo": COMBO,
                "mail_mode": "outlook",
                "reg_mode": "browser",
                "status": status,
                "error": None,
                "password": None,
                "session_path": None,
                "created_at": now,
                "started_at": None,
                "finished_at": None,
            }
        )
    if repo.reap_orphans() != 2:
        failures.append("reap_orphans không dọn đúng 2 job")
    if repo.get("o-run")["status"] != "cancelled" or repo.get("o-queue")["status"] != "cancelled":
        failures.append("reap_orphans bỏ sót job")
    if repo.get("o-ok")["status"] != "success":
        failures.append("reap_orphans đụng vào job đã xong")


def _check_fallback_boolean_contract(failures: list[str]) -> None:
    """API chỉ nhận JSON boolean; chuỗi `false` không được biến thành True."""
    from fastapi import HTTPException
    from gpt_reg.web import server

    parser = getattr(server, "_json_bool", None)
    if parser is None:
        failures.append("server thiếu parser _json_bool cho fallback_enabled")
        return
    if parser({}, "fallback_enabled") is not False:
        failures.append("fallback_enabled thiếu field không mặc định False")
    if parser({"fallback_enabled": True}, "fallback_enabled") is not True:
        failures.append("fallback_enabled=True không được giữ nguyên")
    for bad in ("false", "true", 0, 1, None):
        try:
            parser({"fallback_enabled": bad}, "fallback_enabled")
        except HTTPException as exc:
            if exc.status_code != 400:
                failures.append(f"fallback_enabled={bad!r} trả HTTP {exc.status_code}")
        else:
            failures.append(f"fallback_enabled={bad!r} không bị từ chối")


def _check_identity_not_exposed(failures: list[str]) -> None:
    from gpt_reg.web import server
    from gpt_reg.web.jobs.reg_manager import RegJobManager

    sensitive = {
        "combo",
        "password",
        "session_path",
        "fingerprint_seed",
        "fingerprint_profile",
        "fingerprint_data",
    }
    row = {
        "id": "private-job",
        "email": "private@x.test",
        "combo": COMBO,
        "mail_mode": "outlook",
        "reg_mode": "http",
        "status": "success",
        "password": "secret-password",
        "session_path": "secret-session.json",
        "fingerprint_seed": "81" * 16,
        "fingerprint_profile": "chrome124",
        "fingerprint_data": '{"private":true}',
        "error": 'upstream {"session_token":"error-secret"}',
        "created_at": time.time(),
    }
    public = server._job_for_api(row)
    leaked_api = sorted(sensitive.intersection(public))
    if leaked_api:
        failures.append(f"job API lo fingerprint/secret: {leaked_api}")
    if "error-secret" in str(public.get("error")):
        failures.append("job API lo secret trong error")

    repo = _fresh_repo()
    repo.create(row)
    events: list[dict] = []
    manager = RegJobManager()
    manager.subscribe(events.append)
    manager._finish_job(
        repo,
        row["id"],
        status="success",
        password="secret-password",
        session_path="secret-session.json",
        error='callback?code=finish-secret',
    )
    event = events[-1] if events else {}
    leaked_sse = sorted(sensitive.intersection(event))
    if leaked_sse:
        failures.append(f"job SSE lo fingerprint/secret: {leaked_sse}")
    if "finish-secret" in str(event.get("error")):
        failures.append("job SSE lo secret trong error")
    snapshot = manager.snapshot_for_sse([row])
    snapshot_job = (snapshot.get("jobs") or [{}])[0]
    leaked_snapshot = sorted(sensitive.intersection(snapshot_job))
    if leaked_snapshot:
        failures.append(f"job SSE snapshot lo fingerprint/secret: {leaked_snapshot}")
    if "error-secret" in str(snapshot_job.get("error")):
        failures.append("job SSE snapshot lo secret trong error")


def main() -> int:
    failures: list[str] = []
    _check_cancel(failures)
    _check_retry_and_clear(failures)
    _check_delete_selected_ids(failures)
    _check_reap_orphans(failures)
    _check_fallback_boolean_contract(failures)
    _check_identity_not_exposed(failures)
    for line in failures:
        print(f"[fail] {line}")
    print("[fail] job api" if failures else "[ok] job api")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
