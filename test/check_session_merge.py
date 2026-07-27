"""Session file phải GỘP, không được ghi đè.

`runtime/sessions/<email>.json` là nguồn DUY NHẤT của hai thứ không tái tạo được:

  - `mfa_secret` — mất là account bật 2FA vĩnh viễn không đăng nhập lại được.
  - `password`   — mật khẩu tài khoản ChatGPT (khác mật khẩu hộp thư trong combo).

Bản đầu ghi đè thẳng. Một lần retry thành công (không bật 2FA nên
`result.mfa_secret=None`) đã xoá sổ secret của BraunbergerKlare74@hotmail.com —
đăng nhập được đúng một lần rồi mất luôn đường quay lại. Test này chốt lại:

  - field mới rỗng → giữ nguyên field cũ;
  - `mfa_activated` đang True không bị hạ xuống False bởi lần chạy không đụng 2FA;
  - field mới có giá trị → ghi đè bình thường;
  - ghi nguyên tử (qua file tạm), không để lại file cụt.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
from pathlib import Path

from gpt_reg.config import load_settings
from gpt_reg.models import SignupResult
from gpt_reg.signup import save_session_file


def _result(**kw) -> SignupResult:
    base = dict(ok=True, email="merge_probe@hotmail.com")
    base.update(kw)
    return SignupResult(**base)


def main() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        settings = dataclasses.replace(load_settings(), runtime_dir=Path(tmp))
        target = Path(tmp) / "sessions" / "merge_probe_at_hotmail_com.json"

        # Lần 1: đăng ký + bật 2FA đầy đủ.
        save_session_file(settings=settings, result=_result(
            password="AccountPw123", access_token="tok-1", session_token="sess-1",
            mfa_secret="JBSWY3DPEHPK3PXP", mfa_activated=True,
            cookies=[{"name": "a", "value": "1"}], user_agent="UA/1",
            fingerprint_profile="chrome124",
        ))
        first = json.loads(target.read_text(encoding="utf-8"))
        if first.get("mfa_secret") != "JBSWY3DPEHPK3PXP":
            failures.append("lần ghi đầu đã sai mfa_secret")

        # Lần 2: retry, KHÔNG bật 2FA (mfa_secret=None, mfa_activated=False) —
        # đúng kịch bản đã xoá mất secret thật.
        save_session_file(settings=settings, result=_result(
            password="AccountPw123", access_token="tok-2", session_token="sess-2",
            mfa_secret=None, mfa_activated=False,
            cookies=[{"name": "b", "value": "2"}], user_agent="UA/2",
        ))
        second = json.loads(target.read_text(encoding="utf-8"))

        if second.get("mfa_secret") != "JBSWY3DPEHPK3PXP":
            failures.append(f"retry đã xoá mfa_secret: {second.get('mfa_secret')!r}")
        if second.get("mfa_activated") is not True:
            failures.append("retry hạ mfa_activated True → False")
        if second.get("access_token") != "tok-2":
            failures.append(f"field mới không ghi đè được: {second.get('access_token')!r}")
        if second.get("session_token") != "sess-2":
            failures.append("session_token mới không được ghi")
        if second.get("cookies") != [{"name": "b", "value": "2"}]:
            failures.append(f"cookies mới không ghi đè: {second.get('cookies')!r}")
        if second.get("fingerprint_profile") != "chrome124":
            failures.append("retry da xoa fingerprint_profile trong session file")

        # Lần 3: mật khẩu None (phase không xác định được) → giữ mật khẩu cũ.
        save_session_file(settings=settings, result=_result(
            password=None, access_token="tok-3",
        ))
        third = json.loads(target.read_text(encoding="utf-8"))
        if third.get("password") != "AccountPw123":
            failures.append(f"mật khẩu bị xoá khi giá trị mới None: {third.get('password')!r}")

        # Lần 4: mật khẩu mới khác → phải ghi đè (đổi mật khẩu là hợp lệ).
        save_session_file(settings=settings, result=_result(password="NewAccountPw9"))
        fourth = json.loads(target.read_text(encoding="utf-8"))
        if fourth.get("password") != "NewAccountPw9":
            failures.append("mật khẩu mới không ghi đè được")

        # Không để lại file tạm.
        leftovers = list((Path(tmp) / "sessions").glob("*.tmp"))
        if leftovers:
            failures.append(f"còn file tạm sau khi ghi: {[p.name for p in leftovers]}")

        # File cũ hỏng (JSON lỗi) không được làm sập cả lần ghi.
        target.write_text("{ khong phai json", encoding="utf-8")
        try:
            save_session_file(settings=settings, result=_result(password="AfterCorrupt1"))
            after = json.loads(target.read_text(encoding="utf-8"))
            if after.get("password") != "AfterCorrupt1":
                failures.append("file cũ hỏng thì lần ghi mới cũng hỏng")
        except Exception as exc:
            failures.append(f"file cũ hỏng làm save_session_file nổ: {type(exc).__name__}")

    # BrowserHandoff phải mang được mật khẩu thật về cho signup.
    from gpt_reg.models import BrowserHandoff

    if "account_password" not in BrowserHandoff.model_fields:
        failures.append("BrowserHandoff thiếu account_password — mật khẩu thật không về tới session file")

    src = Path(__file__).resolve().parent.parent / "gpt_reg" / "signup.py"
    if "account_password" not in src.read_text(encoding="utf-8"):
        failures.append("signup.py không dùng account_password của handoff")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] session merge" if failures else "[ok] session merge")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
