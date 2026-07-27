"""Truy vết chuỗi redirect sau khi đã xác thực, tìm chỗ mất `code=`.

Chạy tay (cần mạng + combo thật):

    python test/probe_oauth_callback.py <email>|<pass>|<refresh_token>|<client_id>

Đăng nhập account CŨ bằng đúng đường của http phase, rồi in từng chặng redirect
kèm status + Location để biết `code=` bị đánh rơi ở đâu. Cần thiết vì log job chỉ
nói "callback=missing", không nói chuỗi tắt ở chặng nào.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gpt_reg.config import ensure_runtime_dirs, load_settings
from gpt_reg.db import connect, migrate
from gpt_reg.db.repositories import SettingsRepository
from gpt_reg.fingerprint import profile_for_seed
from gpt_reg.mail.modes import parse_outlook_combo
from gpt_reg.phases import http_reg as hr
from gpt_reg.proxy.pool import ProxyPool


def _log(msg: str) -> None:
    print(msg)


def _trace(session, start: str, limit: int = 15) -> None:
    current = start
    for hop in range(1, limit + 1):
        if "/api/auth/callback/openai" in current and "code=" in current:
            print(f"  [{hop}] ★ CALLBACK có code: {current[:120]}")
            return
        resp = session.get(
            current,
            headers=hr._html_headers(session, "https://chatgpt.com/"),
            timeout=30,
            allow_redirects=False,
        )
        loc = (resp.headers.get("Location") or "").strip()
        print(f"  [{hop}] {resp.status_code} {current.split('?')[0][:80]}")
        if loc:
            print(f"       → Location: {loc[:120]}")
        if resp.status_code not in (301, 302, 303, 307, 308) or not loc:
            body = (resp.text or "")[:300].replace("\n", " ")
            print(f"       ✖ chuỗi dừng. body[:300]={body!r}")
            return
        if loc.startswith("/"):
            from urllib.parse import urlparse

            p = urlparse(current)
            loc = f"{p.scheme}://{p.netloc}{loc}"
        current = loc
    print("  ✖ quá số chặng")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    combo_raw = sys.argv[1]
    combo = parse_outlook_combo(combo_raw)
    email = combo.email
    password = combo_raw.split("|")[1]

    settings = load_settings()
    ensure_runtime_dirs(settings)
    conn = connect(settings.runtime_dir / "data.db")
    migrate(conn)
    proxy = ProxyPool.from_multiline(SettingsRepository(conn).get("proxy.pool") or "").acquire_url()

    fingerprint_seed = hashlib.sha256(
        f"probe-oauth:{email.strip().lower()}".encode("utf-8")
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
        print(f"auth_url = {auth_url[:110]}")
        if hr.classify_landing(landing) != "login":
            print("!! account không ở màn /log-in/password — probe này chỉ dùng cho acc cũ")
            return 1

        login = hr._step_login_password(session, password, device_id, _log)
        page_type = login.get("page_type") or ""
        cont = login.get("continue_url") or ""
        print(f"page_type={page_type!r} continue_url={cont[:100]!r}")

        if page_type == "mfa_challenge" or "/mfa-challenge" in cont:
            secret = hr._saved_mfa_secret(email)
            print(f"mfa_secret: {'có' if secret else 'KHÔNG CÓ'}")
            if not secret:
                return 1
            cont = hr._step_mfa_challenge(session, secret, cont, device_id, _log) or cont
            print(f"sau MFA → continue_url={cont[:100]!r}")

        print("\n--- chuỗi từ continue_url:")
        _trace(session, cont or "https://chatgpt.com/")
        print("\n--- chuỗi từ authorize URL (replay):")
        _trace(session, auth_url)
        print("\n--- /api/auth/session:")
        st, at, uid, mail_ = hr._get_session_tokens(session, _log)
        print(f"session_token={'có' if st else 'không'} access_token={'có' if at else 'không'} email={mail_!r}")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
