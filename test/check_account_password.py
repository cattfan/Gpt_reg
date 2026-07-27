"""Mật khẩu TÀI KHOẢN vs mật khẩu HỘP THƯ — không được lẫn nhau.

Combo Hotmail có dạng `email|mail_pass|refresh_token|client_id`, nên `mail_pass`
là mật khẩu **hộp thư**. Mật khẩu **tài khoản ChatGPT** do tool tự sinh
(`secrets.token_urlsafe`) và được ghi lại ngay khi `user/register` trả 200.

Đo thật trên BraunbergerKlare74@hotmail.com: combo 9 ký tự, account 12 ký tự.
Đường retry lúc đầu dựng lại request từ combo và bỏ qua mật khẩu đã ghi → nộp
mật khẩu hộp thư vào `/api/accounts/password/verify` → 401
`invalid_username_or_password`, acc không cứu được dù mật khẩu thật đang nằm sẵn
trong DB.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from gpt_reg.mail.providers import build_request_from_combo
from gpt_reg.phases import http_reg as hr


COMBO = (
    "Someone123@hotmail.com|mailpw123|refresh-token-xyz|"
    "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
)


def main() -> int:
    failures: list[str] = []

    # 1. password_override thắng mật khẩu combo.
    email, pw = build_request_from_combo(COMBO, password_override="AccountPw12345")
    if email != "Someone123@hotmail.com":
        failures.append(f"email parse sai: {email}")
    if pw != "AccountPw12345":
        failures.append(f"password_override bị bỏ qua: {pw!r}")

    # 2. Không có override thì mới dùng mật khẩu combo (có thể được đệm cho đủ
    #    điều kiện độ mạnh — xem ensure_min_password).
    _, pw2 = build_request_from_combo(COMBO)
    if not (pw2 or "").startswith("mailpw123"):
        failures.append(f"không override mà mật khẩu combo sai: {pw2!r}")

    # 3. reg_manager phải truyền mật khẩu đã ghi vào override. Đọc source vì
    #    _run_one cần cả jobs_repo + thread pool để gọi thật.
    mgr = Path(hr.__file__).resolve().parent.parent / "web" / "jobs" / "reg_manager.py"
    text = mgr.read_text(encoding="utf-8")
    if "password_override" not in text:
        failures.append(
            "reg_manager không truyền password_override — retry sẽ nộp mật khẩu "
            "hộp thư vào password/verify và bị 401"
        )
    else:
        if 'row.get("password")' not in text and "row['password']" not in text:
            failures.append("password_override không lấy từ row['password'] của DB")

    # 4. http_reg vẫn có đường đọc mật khẩu account từ session file (dùng cho
    #    CLI, nơi không có DB job).
    if not hasattr(hr, "_saved_session_field"):
        failures.append("thiếu _saved_session_field — CLI mất đường lấy mật khẩu account")
    else:
        sig = inspect.signature(hr._saved_session_field)
        if list(sig.parameters) != ["email", "field"]:
            failures.append(f"_saved_session_field signature lạ: {sig}")

    # 5. Sai mật khẩu phải phân loại step='wrong_password' để caller thử mật khẩu
    #    còn lại thay vì bỏ cuộc.
    src = Path(hr.__file__).read_text(encoding="utf-8")
    if 'step != "wrong_password"' not in src:
        failures.append("vòng thử mật khẩu không dựa vào step='wrong_password'")

    # 6. Session file: đọc được password + mfa_secret, thiếu file thì None chứ
    #    không nổ.
    if hr._saved_session_field("khong-ton-tai-9999@hotmail.com", "password") is not None:
        failures.append("email không có session file mà vẫn trả mật khẩu")

    from gpt_reg.config import load_settings

    sessions = load_settings().sessions_dir
    sessions.mkdir(parents=True, exist_ok=True)
    probe = sessions / "check_pw_probe_at_hotmail_com.json"
    probe.write_text(
        json.dumps({"email": "check_pw_probe@hotmail.com", "password": "Sess12345678",
                    "mfa_secret": "JBSWY3DPEHPK3PXP"}),
        encoding="utf-8",
    )
    try:
        got = hr._saved_session_field("check_pw_probe@hotmail.com", "password")
        if got != "Sess12345678":
            failures.append(f"không đọc được mật khẩu từ session file: {got!r}")
        if hr._saved_mfa_secret("check_pw_probe@hotmail.com") != "JBSWY3DPEHPK3PXP":
            failures.append("không đọc được mfa_secret từ session file")
    finally:
        probe.unlink(missing_ok=True)

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] account password" if failures else "[ok] account password")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
