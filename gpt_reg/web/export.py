"""Định dạng kết quả đăng ký để xuất.

Ba định dạng:
  - `combo`      : email|password|totp_secret
  - `combo_mail` : email|password|totp_secret|<combo mail gốc>
  - `json`       : mảng object đầy đủ

TOTP secret nằm trong session file (`mfa_secret`), không lưu trùng trong DB.
Combo mail gốc lấy từ cột `jobs.combo` (email|password|refresh_token|client_id).
Việc lộ combo gốc là **có chủ đích** ở đây — người dùng chủ động bấm xuất — khác
với `/api/jobs` vốn đã lọc bỏ combo khỏi luồng polling.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable

SecretReader = Callable[[str], str | None]


def build_records(
    jobs: Iterable[dict[str, Any]],
    *,
    read_secret: SecretReader,
) -> list[dict[str, Any]]:
    """Gom mỗi job thành 1 bản ghi xuất. `read_secret(session_path)` trả TOTP
    secret hoặc None."""
    records: list[dict[str, Any]] = []
    for job in jobs:
        session_path = job.get("session_path")
        secret = read_secret(session_path) if session_path else None
        records.append(
            {
                "email": job.get("email") or "",
                "password": job.get("password") or "",
                "totp_secret": secret or "",
                "mail_combo": (job.get("combo") or "").strip(),
                "status": job.get("status"),
                "error": job.get("error"),
                "mfa_activated": bool(job.get("mfa_activated")),
                "session_path": session_path,
            }
        )
    return records


def format_combo(records: list[dict[str, Any]], *, include_mail_full: bool) -> str:
    lines: list[str] = []
    for r in records:
        parts = [r["email"], r["password"], r["totp_secret"]]
        if include_mail_full:
            parts.append(r["mail_combo"])
        lines.append("|".join(parts))
    return "\n".join(lines)


def format_json(records: list[dict[str, Any]]) -> str:
    payload = [
        {
            "email": r["email"],
            "password": r["password"],
            "totp_secret": r["totp_secret"],
            "mail_combo": r["mail_combo"],
            "mfa_activated": r["mfa_activated"],
            "session_path": r["session_path"],
        }
        for r in records
    ]
    return json.dumps(payload, indent=2, ensure_ascii=False)


FORMATS = ("combo", "combo_mail", "json")


def render(records: list[dict[str, Any]], fmt: str) -> tuple[str, str]:
    """Return (text, media_type)."""
    if fmt == "json":
        return format_json(records), "application/json"
    if fmt == "combo_mail":
        return format_combo(records, include_mail_full=True), "text/plain"
    return format_combo(records, include_mail_full=False), "text/plain"


def read_secret_from_session_file(session_path: str | None) -> str | None:
    """Đọc `mfa_secret` từ session JSON. Lỗi đọc → None, không làm hỏng cả export."""
    if not session_path:
        return None
    try:
        from pathlib import Path

        data = json.loads(Path(session_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    secret = data.get("mfa_secret")
    return str(secret) if secret else None
