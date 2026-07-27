"""Vượt `mfa_challenge` bằng TOTP đã lưu — hợp đồng payload của mfa/verify.

Account đã bật 2FA thì MỌI lần đăng nhập sau đều dừng ở `mfa_challenge`, nên
không vượt được nghĩa là không bao giờ retry được acc đó nữa.

Hợp đồng payload dò bằng `test/probe_mfa_verify.py`, server báo thiếu từng field
một:

    {"code": ...}                          → 400 missing_required_parameter param="type"
    {"type": "totp", "code": ...}          → 400 missing_required_parameter param="id"
    {"type": "totp", "code": ..., "id": …} → 200 + continue_url có sẵn `code=`

`id` là đoạn cuối của `/mfa-challenge/<id>` trong continue_url mà password/verify
trả về. Vì trả thẳng callback có `code=`, đường này KHÔNG cần chạy lại authorize.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from gpt_reg.phases.http_reg import _step_mfa_challenge, mfa_challenge_id


ID_CASES = [
    ("https://auth.openai.com/mfa-challenge/6a66090240b481919153cc3201b42396",
     "6a66090240b481919153cc3201b42396"),
    ("https://auth.openai.com/mfa-challenge/abc123?flow=login", "abc123"),
    ("https://auth.openai.com/mfa-challenge/abc123/", "abc123"),
    ("https://auth.openai.com/log-in/password", ""),
    ("", ""),
]


class _FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = "fake"

    def json(self):
        return self._payload


class _FakeSession:
    """Ghi lại payload gửi đi để kiểm tra đúng ba field bắt buộc."""

    def __init__(self, status=200, payload=None):
        self.sent = None
        self._status = status
        self._payload = payload or {
            "continue_url": "https://chatgpt.com/api/auth/callback/openai?code=ac_x&state=y"
        }
        self.gpt_profile = None

    def post(self, url, headers=None, json=None, timeout=None):
        self.sent = {"url": url, "headers": headers or {}, "json": json or {}}
        return _FakeResp(self._status, self._payload)


def main() -> int:
    failures: list[str] = []

    for url, want in ID_CASES:
        got = mfa_challenge_id(url)
        if got != want:
            failures.append(f"mfa_challenge_id({url!r}) = {got!r}, cần {want!r}")

    challenge = "https://auth.openai.com/mfa-challenge/6a66090240b481919153cc3201b42396"

    # Payload phải có đủ type + code + id, và id lấy từ URL challenge.
    sess = _FakeSession()
    cont = _step_mfa_challenge(sess, "JBSWY3DPEHPK3PXP", challenge, "dev-1", lambda _m: None)
    body = sess.sent["json"]
    for field in ("type", "code", "id"):
        if field not in body:
            failures.append(f"payload mfa/verify thiếu {field!r} — server trả 400")
    if body.get("type") != "totp":
        failures.append(f"type sai: {body.get('type')!r} (server nhận 'totp')")
    if body.get("id") != "6a66090240b481919153cc3201b42396":
        failures.append(f"id không lấy từ challenge URL: {body.get('id')!r}")
    code = str(body.get("code") or "")
    if not (len(code) == 6 and code.isdigit()):
        failures.append(f"code không phải 6 số: {code!r}")
    if sess.sent["url"] != "https://auth.openai.com/api/accounts/mfa/verify":
        failures.append(f"URL sai: {sess.sent['url']}")
    if "code=" not in cont:
        failures.append(f"không trả callback có code=: {cont!r}")

    # Không có id → lỗi rõ ràng, không gửi request mù.
    sess2 = _FakeSession()
    try:
        _step_mfa_challenge(sess2, "JBSWY3DPEHPK3PXP", "https://auth.openai.com/x", "d", lambda _m: None)
        failures.append("challenge URL không có id mà vẫn gửi request")
    except Exception as exc:
        if getattr(exc, "step", "") != "mfa":
            failures.append(f"lỗi thiếu id phân loại sai: step={getattr(exc, 'step', None)!r}")

    # Caller phải truyền continue_url vào (nếu không thì không có id).
    sig = inspect.signature(_step_mfa_challenge)
    if "challenge_url" not in sig.parameters:
        failures.append(f"_step_mfa_challenge không nhận challenge_url: {sig}")

    src = Path(__file__).resolve().parent.parent / "gpt_reg" / "phases" / "http_reg.py"
    text = src.read_text(encoding="utf-8")
    if "_saved_mfa_secret(request.email)" not in text:
        failures.append("nhánh login không đọc mfa_secret đã lưu")
    if 'page_type == "mfa_challenge"' not in text:
        failures.append("nhánh login không nhận diện page_type='mfa_challenge'")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] mfa challenge" if failures else f"[ok] mfa challenge {len(ID_CASES)} id + payload")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
