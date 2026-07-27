"""Kiểm tra định dạng xuất kết quả (combo / combo_mail / json)."""

from __future__ import annotations

import json

from gpt_reg.web import export

CID = "12345678-1234-1234-1234-123456789abc"
JOBS = [
    {
        "email": "a@hotmail.com",
        "password": "PassA123456",
        "combo": f"a@hotmail.com|MailPassA|refreshA|{CID}",
        "session_path": "/x/a.json",
        "status": "success",
        "error": None,
        "mfa_activated": 1,
    },
    {
        "email": "b@hotmail.com",
        "password": "PassB123456",
        "combo": f"b@hotmail.com|MailPassB|refreshB|{CID}",
        "session_path": None,  # không có secret
        "status": "success",
        "error": None,
        "mfa_activated": 0,
    },
]

SECRETS = {"/x/a.json": "JBSWY3DPEHPK3PXP"}


def main() -> int:
    failures: list[str] = []
    records = export.build_records(JOBS, read_secret=lambda p: SECRETS.get(p))

    combo, media = export.render(records, "combo")
    if media != "text/plain":
        failures.append(f"combo media {media}")
    expected_combo = "a@hotmail.com|PassA123456|JBSWY3DPEHPK3PXP\nb@hotmail.com|PassB123456|"
    if combo != expected_combo:
        failures.append(f"combo sai:\n{combo!r}\nmuốn:\n{expected_combo!r}")

    combo_mail, _ = export.render(records, "combo_mail")
    first = combo_mail.split("\n")[0]
    if first != f"a@hotmail.com|PassA123456|JBSWY3DPEHPK3PXP|a@hotmail.com|MailPassA|refreshA|{CID}":
        failures.append(f"combo_mail sai: {first!r}")

    as_json, media = export.render(records, "json")
    if media != "application/json":
        failures.append(f"json media {media}")
    data = json.loads(as_json)
    if data[0]["totp_secret"] != "JBSWY3DPEHPK3PXP" or data[0]["mail_combo"] != JOBS[0]["combo"]:
        failures.append(f"json record sai: {data[0]}")
    if data[1]["totp_secret"] != "":
        failures.append("json: job không secret phải để rỗng")

    # combo mặc định KHÔNG lộ combo mail gốc.
    if "refreshA" in combo:
        failures.append("combo mặc định lộ refresh token")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] export" if failures else "[ok] export")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
