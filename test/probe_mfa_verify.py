"""Dò đúng payload/endpoint của bước vượt `mfa_challenge`.

Chạy tay:

    python test/probe_mfa_verify.py "<email>|<account_pass>|<refresh>|<client_id>"

`POST /api/accounts/mfa/verify` với `{"code": ...}` trả 400
`missing_required_parameter` (param="type"), nên payload còn thiếu ít nhất một
field. Probe này đăng nhập thật rồi thử lần lượt các biến thể để biết server
nhận cái nào — không đoán.

Mỗi biến thể tiêu tốn một mã TOTP. Mã TOTP đổi mỗi 30s và server có thể từ chối
mã đã dùng, nên probe sinh mã MỚI trước từng lần thử và chờ sang cửa sổ mới nếu
mã hiện tại sắp hết hạn.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gpt_reg import totp_helper
from gpt_reg.config import ensure_runtime_dirs, load_settings
from gpt_reg.db import connect, migrate
from gpt_reg.db.repositories import SettingsRepository
from gpt_reg.fingerprint import profile_for_seed
from gpt_reg.mail.modes import parse_outlook_combo
from gpt_reg.phases import http_reg as hr
from gpt_reg.proxy.pool import ProxyPool


def _log(msg: str) -> None:
    print(msg)


# (đường dẫn, hàm dựng payload từ (code, challenge_id)). Khả năng cao → thấp.
#
# Server đã xác nhận từng bước: thiếu `type` → 400 param="type"; thêm `type` →
# 400 param="id". `id` gần như chắc chắn là id của challenge nằm cuối
# continue_url (/mfa-challenge/<id>).
VARIANTS = [
    ("/api/accounts/mfa/verify", lambda c, i: {"type": "totp", "code": c, "id": i}),
    ("/api/accounts/mfa/verify", lambda c, i: {"type": "authenticator", "code": c, "id": i}),
    ("/api/accounts/mfa/verify", lambda c, i: {"type": "totp", "code": c, "id": i,
                                               "remember_device": False}),
    ("/api/accounts/mfa/verify", lambda c, i: {"type": "software_token", "code": c, "id": i}),
]


def _fresh_code(secret: str) -> str:
    """Mã TOTP ở đầu cửa sổ 30s — tránh mã chết giữa đường bay."""
    remaining = totp_helper.time_remaining()
    if remaining < 8:
        print(f"  (chờ {remaining + 1}s sang cửa sổ TOTP mới)")
        time.sleep(remaining + 1)
    return totp_helper.generate_code(secret)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    combo_raw = sys.argv[1].strip()
    combo = parse_outlook_combo(combo_raw)
    email = combo.email
    password = combo_raw.split("|")[1]

    secret = hr._saved_mfa_secret(email)
    if not secret:
        print(f"!! không có mfa_secret cho {email} trong runtime/sessions — dừng")
        return 1

    settings = load_settings()
    ensure_runtime_dirs(settings)
    conn = connect(settings.runtime_dir / "data.db")
    migrate(conn)
    proxy = ProxyPool.from_multiline(SettingsRepository(conn).get("proxy.pool") or "").acquire_url()

    fingerprint_seed = hashlib.sha256(
        f"probe-mfa:{email.strip().lower()}".encode("utf-8")
    ).hexdigest()[:32]
    session, device_id, landing, auth_url = hr._bootstrap_with_profile_rotation(
        proxy,
        _log,
        fingerprint_seed=fingerprint_seed,
        preferred_profile=profile_for_seed(fingerprint_seed).name,
        login_hint=email,
    )
    try:
        kind = hr.classify_landing(landing)
        print(f"\nlanding = {kind} ({landing[:90]})")
        if kind != "login":
            print("!! account không ở màn /log-in/password — probe này chỉ dùng cho acc có 2FA")
            return 1

        login = hr._step_login_password(session, password, device_id, _log)
        page_type = login.get("page_type") or ""
        cont = login.get("continue_url") or ""
        print(f"page_type={page_type!r}")
        print(f"continue_url={cont[:110]!r}")
        if page_type != "mfa_challenge" and "/mfa-challenge" not in cont:
            print("!! account không dừng ở mfa_challenge — không có gì để dò")
            return 1

        # Nhiều API OpenAI nhét id của challenge vào URL; thử cả biến thể có id.
        challenge_id = cont.rstrip("/").rsplit("/", 1)[-1] if "/mfa-challenge/" in cont else ""
        print(f"challenge_id={challenge_id!r}")

        headers_base = hr._common_headers(session, cont or "https://auth.openai.com/mfa-challenge")
        for path, build in VARIANTS:
            code = _fresh_code(secret)
            payload = build(code, challenge_id)
            headers = dict(headers_base)
            headers["Content-Type"] = "application/json"
            if device_id:
                headers["oai-device-id"] = device_id
            url = f"https://auth.openai.com{path}"
            try:
                resp = session.post(url, headers=headers, json=payload, timeout=30)
            except Exception as exc:
                print(f"  {path} {payload} → LỖI MẠNG {type(exc).__name__}: {exc}")
                continue
            body = (resp.text or "")[:260].replace("\n", " ")
            keys = list(payload)
            print(f"  {path} keys={keys} → HTTP {resp.status_code} {body}")
            if resp.status_code == 200:
                print("\n★ BIẾN THỂ ĐÚNG:", path, json.dumps({k: ("<code>" if k == "code" else v)
                                                              for k, v in payload.items()}))
                try:
                    print("  continue_url =", (resp.json() or {}).get("continue_url"))
                except Exception:
                    pass
                return 0
        print("\n✖ không biến thể nào trả 200")
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
