"""Trạng thái thật của account đăng ký nửa chừng SAU khi verify email.

Chạy tay:  python test/probe_verify_complete.py "<combo gốc>"

Ba account (TouchRockett/Soltmann/MalanderOz) đăng ký dở ở session trước, giờ
landing=otp. Probe này chạy đúng đường: bootstrap → send OTP → validate → rồi
DUMP mọi thứ (validate response, thử create_account, thử authorize lại,
/api/auth/session) để biết account đang kẹt ở đâu và có cần reset mật khẩu không.

KHÔNG sửa gì — chỉ đọc. Tốn một mã OTP.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gpt_reg.config import ensure_runtime_dirs, load_settings
from gpt_reg.db import connect, migrate
from gpt_reg.db.repositories import SettingsRepository
from gpt_reg.fingerprint import profile_for_seed
from gpt_reg.mail.modes import parse_outlook_combo
from gpt_reg.mail.outlook import OutlookMailProvider
from gpt_reg.phases import http_reg as hr
from gpt_reg.proxy.pool import ProxyPool


def _log(msg: str) -> None:
    print(msg)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    combo_raw = sys.argv[1].strip()
    combo = parse_outlook_combo(combo_raw)
    email = combo.email

    settings = load_settings()
    ensure_runtime_dirs(settings)
    conn = connect(settings.runtime_dir / "data.db")
    migrate(conn)
    proxy = ProxyPool.from_multiline(SettingsRepository(conn).get("proxy.pool") or "").acquire_url()
    mail = OutlookMailProvider(combo=combo, state_dir=settings.outlook_state_dir, proxy_url=proxy)

    fingerprint_seed = hashlib.sha256(
        f"probe-verify:{email.strip().lower()}".encode("utf-8")
    ).hexdigest()[:32]
    session, device_id, landing, auth_url = hr._bootstrap_with_profile_rotation(
        proxy,
        _log,
        fingerprint_seed=fingerprint_seed,
        preferred_profile=profile_for_seed(fingerprint_seed).name,
        login_hint=email,
    )
    try:
        print(f"\nlanding = {hr.classify_landing(landing)} ({landing[:90]})")
        if hr.classify_landing(landing) != "otp":
            print("!! account không ở email-verification — probe này dành cho acc landing=otp")
            return 1

        since = datetime.now(timezone.utc)
        hr._step_send_otp(session, device_id, _log)
        code, _ = hr._poll_otp(mail,
                               type("R", (), {"email": email,
                                              "otp_timeout_seconds": 180.0,
                                              "otp_poll_interval_seconds": 5.0})(),
                               since, set(), None, _log)
        print(f"OTP = {code}")
        vr = hr._step_verify_otp(session, code, device_id, _log)
        print("\n=== validate response:")
        print(json.dumps({k: v for k, v in vr.items() if not k.startswith("_")}, indent=2)[:800])

        cont = (vr.get("continue_url") or "").strip()
        page = (vr.get("page") or {}).get("type") if isinstance(vr.get("page"), dict) else None
        print(f"\npage.type = {page!r}")
        print(f"continue_url = {cont[:110]!r}")

        print("\n=== thử create_account (xem account đã đủ hồ sơ chưa):")
        try:
            new_cont = hr._step_create_account(session, "Probe User", "1998-05-14", device_id, _log)
            print(f"  create_account OK → {new_cont[:100]!r}")
            cont = new_cont
        except hr.HttpRegError as exc:
            print(f"  create_account lỗi: {exc}")

        print("\n=== chuỗi từ continue_url:")
        for start_name, start in (("continue_url", cont), ("authorize", auth_url)):
            print(f"  -- {start_name}: {start[:80]}")
            cb = hr._step_follow_redirects(session, start or "https://chatgpt.com/", _log)
            print(f"     → callback = {cb[:90]!r}")
            if cb:
                break

        print("\n=== /api/auth/session:")
        st, at, uid, mail_ = hr._get_session_tokens(session, _log)
        print(f"  session_token={'CÓ' if st else 'không'}  access_token={'CÓ' if at else 'không'}  email={mail_!r}")
        if at:
            print("  → account CỨU ĐƯỢC không cần mật khẩu")
        else:
            print("  → email-verification KHÔNG đủ để ra session; cần reset mật khẩu hoặc mật khẩu account")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
