"""Dò bước register của HTTP reg — in nguyên văn response để chẩn đoán.

Chạy tay:  .venv311\\Scripts\\python test\\probe_http_register.py <combo-file>

Không gửi OTP, không tạo account: chỉ chạy tới `user/register` rồi in đủ
status + body + cookie, đủ để phân biệt "email đã đăng ký" với "state machine
sai bước".
"""

from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path


def main() -> int:
    combo_file = Path(sys.argv[1] if len(sys.argv) > 1 else "runtime/live_batch.txt")
    line = ""
    for raw in combo_file.read_text(encoding="utf-8-sig").splitlines():
        if raw.strip():
            line = raw.strip()
            break
    if not line:
        print("combo file rỗng")
        return 1

    from gpt_reg.config import load_settings
    from gpt_reg.db import connect, migrate
    from gpt_reg.db.repositories import SettingsRepository
    from gpt_reg.fingerprint import profile_for_seed
    from gpt_reg.mail.providers import build_request_from_combo
    from gpt_reg.phases import http_reg as hr
    from gpt_reg.proxy.pool import ProxyPool

    # Dùng đúng hàm của flow thật: nó pad mật khẩu lên tối thiểu 12 ký tự
    # (ChatGPT từ chối < 12). Parse thô sẽ tạo ra lỗi giả.
    email, password = build_request_from_combo(line)

    class _Combo:
        pass

    combo = _Combo()
    combo.email = email
    combo.password = password
    settings = load_settings()
    conn = connect(settings.runtime_dir / "data.db")
    migrate(conn)
    proxy = ProxyPool.from_multiline(SettingsRepository(conn).get("proxy.pool") or "").acquire_url()

    def log(msg: str) -> None:
        print("   ", msg)

    print(f"email: {combo.email}  (mật khẩu {len(combo.password or '')} ký tự sau khi pad)")
    fingerprint_seed = hashlib.sha256(
        f"probe-http-register:{combo.email.strip().lower()}".encode("utf-8")
    ).hexdigest()[:32]
    session, device_id, _landing, auth_url = hr._bootstrap_with_profile_rotation(
        proxy,
        log,
        fingerprint_seed=fingerprint_seed,
        preferred_profile=profile_for_seed(fingerprint_seed).name,
        login_hint=combo.email,
    )
    profile = hr._profile_of(session)
    print(f"profile: {profile.name} ({profile.impersonate})")
    print(f"device_id: {device_id}")
    print(f"auth_url: {auth_url[:110]}")

    try:
        # A. authorize/continue — bước GSH BỎ QUA. Xem server nói gì.
        sentinel = hr._get_sentinel_token(session, device_id, "authorize_continue", log)
        headers = hr._common_headers(session, "https://auth.openai.com/create-account")
        headers["Content-Type"] = "application/json"
        if sentinel:
            headers["openai-sentinel-token"] = sentinel
        if device_id:
            headers["oai-device-id"] = device_id
        resp = session.post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers=headers,
            json={"username": {"value": combo.email, "kind": "email"}, "screen_hint": "signup"},
            timeout=30,
        )
        print(f"\n== authorize/continue -> HTTP {resp.status_code}")
        print((resp.text or "")[:700])

        # B. GET trang password rồi register — đúng thứ tự hiện tại của http_reg.
        session.get(
            "https://auth.openai.com/create-account/password",
            headers=hr._common_headers(session, "https://auth.openai.com/create-account"),
            timeout=15,
        )
        sentinel2 = hr._get_sentinel_token(session, device_id, "username_password_create", log)
        reg_headers = hr._common_headers(session, "https://auth.openai.com/create-account/password")
        reg_headers["Content-Type"] = "application/json"
        if sentinel2:
            reg_headers["openai-sentinel-token"] = sentinel2
        if device_id:
            reg_headers["oai-device-id"] = device_id
        reg = session.post(
            "https://auth.openai.com/api/accounts/user/register",
            headers=reg_headers,
            json={"password": combo.password, "username": combo.email},
            timeout=30,
        )
        print(f"\n== user/register -> HTTP {reg.status_code}")
        body = reg.text or ""
        print(body[:900])
        try:
            parsed = json.loads(body)
            print("\nkeys:", sorted(parsed) if isinstance(parsed, dict) else type(parsed).__name__)
        except Exception:
            pass

        names = sorted({c.name for c in session.cookies.jar})
        print(f"\ncookies ({len(names)}): {', '.join(names[:18])}")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
