"""Chọn ĐÚNG loại mã OTP khi hộp thư có nhiều mail cùng lúc.

OpenAI gửi hai mail khác nhau trong cùng một flow:

    "Your temporary ChatGPT verification code"  → /api/accounts/email-otp/validate
    "Your temporary ChatGPT login code"         → đăng nhập passwordless

Cả hai đều khớp regex 6 số, đều từ cùng người gửi, và **đến cùng một giây** —
snapshot dưới đây là hộp thư thật của TouchRockett622@hotmail.com lúc
2026-07-26T15:13Z. Graph trả newest-first nên mail `login` chen lên trước và
`wait_for_otp` lấy nhầm mã → `/email-otp/validate` trả 401 wrong_email_otp_code
→ đốt một lượt verify + một lượt resend → 429 → job chết vì "không nhận được mã
OTP mới sau 180s". Lỗi trông như rate limit nhưng gốc là chọn nhầm mail.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gpt_reg.mail.outlook import OutlookMailProvider, otp_kind


KIND_CASES = [
    ("Your temporary ChatGPT verification code", "", "verification"),
    ("Your temporary ChatGPT login code", "", "login"),
    ("", "Your verification code is 123456", "verification"),
    ("", "Here is your login code", "login"),
    ("Random subject", "nothing useful", "unknown"),
    # vi-VN: browser_locale mặc định tiếng Việt nên tiêu đề mail cũng tiếng Việt.
    ("Mã đăng nhập ChatGPT tạm thời của bạn", "", "login"),
    ("Mã xác minh tạm thời của bạn cho ChatGPT", "", "verification"),
]

# (phút trước, tiêu đề, mã) — đúng thứ tự Graph trả (newest first).
MAILBOX = [
    (0.0, "Your temporary ChatGPT login code", "493577"),
    (0.0, "Your temporary ChatGPT verification code", "530752"),
    (1.8, "Your temporary ChatGPT verification code", "530752"),
    (1.9, "Your temporary ChatGPT login code", "493577"),
    (9.0, "Your temporary ChatGPT verification code", "111111"),
]


class _FakeProvider(OutlookMailProvider):
    """Chặn phần mạng, giữ nguyên logic chọn mail của wait_for_otp."""

    def __init__(self, messages):
        self._messages = messages
        self._used_otp_codes = set()
        self._seen_verification_otp = False

    def _ensure_access(self, *, log):
        return "fake-token"

    def _client(self):
        class _Null:
            def __enter__(self_inner):
                return None

            def __exit__(self_inner, *a):
                return False

        return _Null()

    def _list_messages(self, client, access_token, *, top=10):
        return self._messages


def _build(now: datetime, rows) -> list[dict]:
    out = []
    for minutes, subject, code in rows:
        received = now - timedelta(minutes=minutes)
        out.append({
            "subject": subject,
            "from": {"emailAddress": {"address": "noreply@tm.openai.com"}},
            "receivedDateTime": received.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": {"content": f"<p>Your code is {code}</p>"},
        })
    return out


def main() -> int:
    failures: list[str] = []
    quiet = lambda _m: None

    for subject, body, want in KIND_CASES:
        got = otp_kind(subject, body)
        if got != want:
            failures.append(f"otp_kind({subject!r}, {body!r}) = {got!r}, cần {want!r}")

    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=3)

    # Mail login đứng đầu danh sách nhưng phải chọn mã verification.
    p = _FakeProvider(_build(now, MAILBOX))
    code = p.wait_for_otp(email="x@hotmail.com", since=since, timeout_s=5, poll_interval_s=0.1, log=quiet)
    if code != "530752":
        failures.append(f"chọn nhầm mã: {code} (cần 530752 — mail verification)")

    # Mã đã dùng phải bị bỏ qua, và KHÔNG được rơi xuống mã login: chờ tiếp còn
    # hơn nộp một mã chắc chắn bị validate từ chối.
    p2 = _FakeProvider(_build(now, MAILBOX))
    p2._used_otp_codes.add("530752")
    try:
        code2 = p2.wait_for_otp(email="x@hotmail.com", since=since, timeout_s=1, poll_interval_s=0.1, log=quiet)
        failures.append(f"hết mã verification lại trả {code2} (mã login) — sẽ 401 ở validate")
    except TimeoutError:
        pass

    # Mail cũ hơn `since` phải bị loại (mã 111111 cách 9 phút).
    p3 = _FakeProvider(_build(now, [MAILBOX[-1]]))
    try:
        stale = p3.wait_for_otp(email="x@hotmail.com", since=since, timeout_s=1, poll_interval_s=0.1, log=quiet)
        failures.append(f"mail cũ hơn since vẫn được nhận: {stale}")
    except TimeoutError:
        pass

    # Chưa từng thấy mail verification → mail login là tất cả những gì có, phải trả.
    p4 = _FakeProvider(_build(now, [MAILBOX[0]]))
    only_login = p4.wait_for_otp(email="x@hotmail.com", since=since, timeout_s=5, poll_interval_s=0.1, log=quiet)
    if only_login != "493577":
        failures.append(f"chỉ có mail login mà không trả: {only_login}")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] otp kind" if failures else f"[ok] otp kind {len(KIND_CASES)} kind + 4 chọn mail")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
