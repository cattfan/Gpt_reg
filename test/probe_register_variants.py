"""Thử các biến thể request `user/register` để tìm cái server chấp nhận.

Bắt được từ browser: bấm "Continue with password" KHÔNG gọi API (chỉ đổi route
SPA), và request register của browser **không gửi** `openai-sentinel-token`.
Script này dò xem biến thể nào qua được.

Mỗi biến thể chạy trên một session sạch vì register thành công sẽ đổi state.

Chạy tay:  .venv311\\Scripts\\python test\\probe_register_variants.py <combo-file>
"""

from __future__ import annotations

import sys
import hashlib
from pathlib import Path


def _fresh(proxy, email, log):
    from gpt_reg.fingerprint import profile_for_seed
    from gpt_reg.phases import http_reg as hr

    fingerprint_seed = hashlib.sha256(
        f"probe-register-variants:{email.strip().lower()}".encode("utf-8")
    ).hexdigest()[:32]
    last = None
    for _ in range(6):
        try:
            session, device_id, _landing, _auth_url = hr._bootstrap_with_profile_rotation(
                proxy,
                log,
                fingerprint_seed=fingerprint_seed,
                preferred_profile=profile_for_seed(fingerprint_seed).name,
                login_hint=email,
            )
            return session, device_id
        except Exception as exc:
            last = exc
    raise RuntimeError(f"bootstrap thất bại: {last}")


def main() -> int:
    combo_file = Path(sys.argv[1] if len(sys.argv) > 1 else "runtime/live_batch.txt")
    line = next(
        (r.strip() for r in combo_file.read_text(encoding="utf-8-sig").splitlines() if r.strip()), ""
    )
    if not line:
        print("combo file rỗng")
        return 1

    from gpt_reg.config import load_settings
    from gpt_reg.db import connect, migrate
    from gpt_reg.db.repositories import SettingsRepository
    from gpt_reg.mail.providers import build_request_from_combo
    from gpt_reg.phases import http_reg as hr
    from gpt_reg.proxy.pool import ProxyPool

    email, password = build_request_from_combo(line)
    settings = load_settings()
    conn = connect(settings.runtime_dir / "data.db")
    migrate(conn)
    proxy = ProxyPool.from_multiline(SettingsRepository(conn).get("proxy.pool") or "").acquire_url()
    quiet = lambda _m: None
    print(f"email: {email}\n")

    # (nhãn, gọi authorize/continue?, GET trang password bằng header HTML?, gửi sentinel?)
    VARIANTS = (
        ("giống browser: no-continue + HTML + no-sentinel", False, True, False),
        ("no-continue + HTML + sentinel", False, True, True),
        ("no-continue + JSON-header + sentinel (bản cũ)", False, False, True),
        ("có-continue + HTML + no-sentinel", True, True, False),
    )

    for label, do_continue, html_page, send_sentinel in VARIANTS:
        try:
            session, device_id = _fresh(proxy, email, quiet)
        except Exception as exc:
            print(f"-- {label}\n   bootstrap lỗi: {exc}")
            continue
        try:
            if do_continue:
                try:
                    hr._step_authorize_continue(session, email, device_id, quiet)
                except Exception as exc:
                    print(f"-- {label}\n   authorize/continue lỗi: {exc}")
                    continue

            headers = (
                hr._html_headers(session, "https://auth.openai.com/email-verification")
                if html_page
                else hr._common_headers(session, "https://auth.openai.com/create-account")
            )
            try:
                session.get(
                    "https://auth.openai.com/create-account/password",
                    headers=headers,
                    timeout=20,
                )
            except Exception:
                pass

            reg_headers = hr._common_headers(
                session, "https://auth.openai.com/create-account/password"
            )
            reg_headers["Content-Type"] = "application/json"
            if device_id:
                reg_headers["oai-device-id"] = device_id
            if send_sentinel:
                token = hr._get_sentinel_token(
                    session, device_id, "username_password_create", quiet
                )
                if token:
                    reg_headers["openai-sentinel-token"] = token

            resp = session.post(
                "https://auth.openai.com/api/accounts/user/register",
                headers=reg_headers,
                json={"password": password, "username": email},
                timeout=30,
            )
            body = (resp.text or "").replace("\n", " ")
            code = ""
            try:
                code = ((resp.json().get("error") or {}).get("code") or "")
            except Exception:
                pass
            mark = "  <<< THÀNH CÔNG" if resp.status_code == 200 else ""
            print(f"-- {label}")
            print(f"   HTTP {resp.status_code} code={code!r}{mark}")
            if resp.status_code != 200:
                print(f"   {body[:170]}")
            else:
                print(f"   {body[:220]}")
                return 0  # đã tìm ra biến thể đúng, dừng để không tạo trùng
        finally:
            session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
