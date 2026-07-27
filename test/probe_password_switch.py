"""Dò cách chuyển từ luồng OTP-không-mật-khẩu sang luồng tạo mật khẩu.

Bối cảnh: `authorize/continue` với `screen_hint=signup` trả
`page.type = email_otp_verification` + `login_methods=[passwordless_otp]`.
Gọi `user/register` ngay lúc đó bị **400 invalid_auth_step**. Trên browser,
người dùng phải bấm "Tiếp tục với mật khẩu" — script này tìm request tương ứng.

Chạy tay:  .venv311\\Scripts\\python test\\probe_password_switch.py <combo-file>
"""

from __future__ import annotations

import json
import hashlib
import re
import sys
from pathlib import Path


def main() -> int:
    combo_file = Path(sys.argv[1] if len(sys.argv) > 1 else "runtime/live_batch.txt")
    line = next((r.strip() for r in combo_file.read_text(encoding="utf-8-sig").splitlines() if r.strip()), "")
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

    email, password = build_request_from_combo(line)
    settings = load_settings()
    conn = connect(settings.runtime_dir / "data.db")
    migrate(conn)
    proxy = ProxyPool.from_multiline(SettingsRepository(conn).get("proxy.pool") or "").acquire_url()

    def log(msg: str) -> None:
        print("   ", msg)

    print(f"email: {email}")
    fingerprint_seed = hashlib.sha256(
        f"probe-password-switch:{email.strip().lower()}".encode("utf-8")
    ).hexdigest()[:32]
    # `invalid_state` xảy ra chập chờn (nghi do proxy đổi IP giữa các kết nối)
    # nên bootstrap lại vài lần cho tới khi vào được state machine.
    session = None
    data = None
    for attempt in range(1, 6):
        if session is not None:
            session.close()
        session, device_id, _landing, _auth_url = hr._bootstrap_with_profile_rotation(
            proxy,
            log,
            fingerprint_seed=fingerprint_seed,
            preferred_profile=profile_for_seed(fingerprint_seed).name,
            login_hint=email,
        )
        try:
            data = hr._step_authorize_continue(session, email, device_id, log)
            break
        except Exception as exc:
            print(f"   lần {attempt}: {exc}")
    if data is None:
        print("không vào được state machine sau 5 lần")
        if session:
            session.close()
        return 1
    try:
        print("\n== authorize/continue payload ==")
        print(json.dumps(data.get("page") or {}, indent=2, ensure_ascii=False)[:600])

        # 1. Trang /email-verification có nút "Continue with password" — xem HTML
        #    tham chiếu route/endpoint nào.
        page = session.get(
            "https://auth.openai.com/email-verification",
            headers=hr._html_headers(session, "https://auth.openai.com/"),
            timeout=25,
        )
        html = page.text or ""
        print(f"\n== GET /email-verification -> HTTP {page.status_code}, {len(html)} bytes")
        paths = sorted(set(re.findall(r"/api/accounts/[a-z0-9_\-/]+", html)))
        print("   API paths trong HTML:", paths[:20] or "(không thấy)")
        for kw in ("create-account/password", "password", "login_method", "auth_method"):
            hits = len(re.findall(kw, html, re.I))
            print(f"   '{kw}': {hits} lần")

        # 2. Thử các payload ứng viên cho authorize/continue.
        candidates = [
            ("screen_hint=create_account_password",
             {"username": {"value": email, "kind": "email"}, "screen_hint": "create_account_password"}),
            ("login_method=password",
             {"username": {"value": email, "kind": "email"}, "screen_hint": "signup",
              "login_method": "password"}),
            ("auth_method=password",
             {"username": {"value": email, "kind": "email"}, "screen_hint": "signup",
              "auth_method": "password"}),
            ("screen_hint=login_password",
             {"username": {"value": email, "kind": "email"}, "screen_hint": "login_password"}),
        ]
        for label, payload in candidates:
            sentinel = hr._get_sentinel_token(session, device_id, "authorize_continue", lambda _m: None)
            headers = hr._common_headers(session, "https://auth.openai.com/email-verification")
            headers["Content-Type"] = "application/json"
            if sentinel:
                headers["openai-sentinel-token"] = sentinel
            if device_id:
                headers["oai-device-id"] = device_id
            resp = session.post(
                "https://auth.openai.com/api/accounts/authorize/continue",
                headers=headers, json=payload, timeout=25,
            )
            body = resp.text or ""
            page_type = ""
            try:
                page_type = ((resp.json().get("page") or {}).get("type") or "")
            except Exception:
                pass
            flag = "  <<< CHUYỂN ĐƯỢC" if "password" in page_type else ""
            print(f"\n-- {label}: HTTP {resp.status_code} page_type={page_type!r}{flag}")
            if resp.status_code != 200:
                print("   ", body[:220].replace("\n", " "))
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
