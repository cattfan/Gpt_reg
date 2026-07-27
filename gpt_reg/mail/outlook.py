from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

import httpx

from gpt_reg.core.exceptions import OutlookComboError, OutlookProviderUnavailable
from gpt_reg.mail.modes import OutlookCombo

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
_DEFAULT_SCOPE = "https://graph.microsoft.com/.default offline_access"
_OTP_REGEX = re.compile(r"\b(\d{6})\b")
_OUTLOOK_HTTP_TIMEOUT = httpx.Timeout(connect=15.0, read=30.0, write=15.0, pool=10.0)
_OUTLOOK_AUTH_FATAL_KEYS = ("invalid_grant", "consent_required", "interaction_required")
_VERIFY_LINK_RE = re.compile(
    r"https://(?:auth\.openai\.com|chatgpt\.com)[^\s\"'<>]+",
    re.IGNORECASE,
)


def _extract_otp(subject: str, body: str) -> str | None:
    cleaned = re.sub(r"<[^>]*>", " ", f"{subject}\n{body}")
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    match = _OTP_REGEX.search(cleaned)
    return match.group(1) if match else None


def _is_openai_sender(sender: str) -> bool:
    s = (sender or "").lower()
    return any(d in s for d in ("openai.com", "auth.openai.com", "noreply@openai", "tm.openai.com"))


def _is_chatgpt_otp_message(sender: str, subject: str, *, body: str = "", recipient: str = "") -> bool:
    subj = (subject or "").lower()
    preview = f"{subj}\n{(body or '')[:800]}".lower()
    if _is_openai_sender(sender):
        return True
    if "openai" in subj or "chatgpt" in subj:
        return True
    if any(
        marker in preview
        for marker in ("chatgpt", "temporary login code", "login code", "verification code")
    ):
        s = (sender or "").lower()
        if "postmaster@outlook.com" in s:
            return True
        mailbox = recipient.strip().lower()
        if mailbox and s == mailbox:
            return True
    return False


_OTP_VERIFICATION_MARKERS = (
    "verification code", "verify your email", "verify your account",
    "xác minh", "xác thực",        # "Mã xác minh tạm thời của bạn cho ChatGPT"
)
_OTP_LOGIN_MARKERS = (
    "login code", "sign-in code", "sign in code",
    "đăng nhập",                    # "Mã đăng nhập ChatGPT tạm thời của bạn"
)


def otp_kind(subject: str, body: str = "") -> str:
    """`verification` | `login` | `unknown` — hai loại mã OTP KHÁC NHAU.

    OpenAI gửi hai mail có tiêu đề khác nhau và mã khác nhau:

      "Your temporary ChatGPT verification code"  → cho /email-otp/validate
      "Your temporary ChatGPT login code"         → cho đăng nhập passwordless

    Cả hai cùng khớp `_OTP_REGEX`, và trong một flow có thể đến **cùng một giây**
    (đo thật: 2026-07-26T15:09:11Z về cả hai). Lấy nhầm mã login rồi đưa vào
    `/email-otp/validate` thì bị 401 wrong_email_otp_code — đốt một lượt verify
    và một lượt resend, resend đủ nhanh thì dính luôn 429.

    Nhận diện cả tiếng Việt: `browser_locale` mặc định `vi-VN` nên OpenAI gửi
    tiêu đề tiếng Việt ("Mã đăng nhập ChatGPT tạm thời", "Mã xác minh tạm thời
    của bạn cho ChatGPT"). Bỏ sót là mọi account vi-VN đều rơi vào `unknown` và
    hỏng bước phân loại — đo thật trên MalanderOz7584@hotmail.com.
    """
    text = f"{subject or ''}\n{(body or '')[:400]}".lower()
    if any(m in text for m in _OTP_VERIFICATION_MARKERS):
        return "verification"
    if any(m in text for m in _OTP_LOGIN_MARKERS):
        return "login"
    return "unknown"


# Thứ tự ưu tiên khi nhiều mail cùng hợp lệ. `unknown` xếp giữa: mail lạ vẫn hơn
# mã login chắc chắn sai endpoint.
_OTP_KIND_RANK = {"verification": 0, "unknown": 1, "login": 2}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None


class OutlookMailProvider:
    def __init__(
        self,
        *,
        combo: OutlookCombo,
        state_dir: Path,
        proxy_url: str | None = None,
    ):
        self.combo = combo
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = state_dir / f"{combo.email.replace('/', '_')}.json"
        self.proxy_url = proxy_url
        self._access_token: str | None = None
        self._used_otp_codes: set[str] = set()
        self._seen_verification_otp = False
        self._access_expires_at: float = 0.0
        self._hydrate_state()

    def _hydrate_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        latest = data.get("refresh_token")
        if isinstance(latest, str) and len(latest) > 20:
            self.combo.refresh_token = latest

    def _persist_state(self, token_data: dict[str, Any]) -> None:
        record = {
            "email": self.combo.email,
            "client_id": self.combo.client_id,
            "refresh_token": self.combo.refresh_token,
            "last_refresh_at": datetime.now(timezone.utc).isoformat(),
            "expires_in": token_data.get("expires_in"),
        }
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def _client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {"timeout": _OUTLOOK_HTTP_TIMEOUT}
        if self.proxy_url:
            kwargs["proxy"] = self.proxy_url
        return httpx.Client(**kwargs)

    def _refresh_access(self, *, log: Callable[[str], None]) -> None:
        log(f"[mail] refresh token for {self.combo.email}")
        with self._client() as client:
            response = client.post(
                _TOKEN_URL,
                data={
                    "client_id": self.combo.client_id,
                    "scope": _DEFAULT_SCOPE,
                    "refresh_token": self.combo.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        if response.status_code != 200:
            body = response.text[:500]
            fatal = any(key in body for key in _OUTLOOK_AUTH_FATAL_KEYS)
            if fatal or 400 <= response.status_code < 500:
                raise OutlookComboError(f"refresh failed HTTP {response.status_code}: {body}")
            raise OutlookProviderUnavailable(f"refresh transient HTTP {response.status_code}: {body[:200]}")
        data = response.json()
        access = data.get("access_token")
        if not access:
            raise OutlookComboError(f"refresh missing access_token: {data}")
        old_refresh = self.combo.refresh_token
        new_refresh = data.get("refresh_token")
        if new_refresh and new_refresh != old_refresh:
            self.combo.refresh_token = new_refresh
        try:
            self._persist_state(data)
        except OSError:
            self.combo.refresh_token = old_refresh
            raise
        self._access_token = access
        self._access_expires_at = time.monotonic() + max(int(data.get("expires_in", 3600)) - 60, 60)

    def _ensure_access(self, *, log: Callable[[str], None]) -> str:
        if self._access_token and time.monotonic() < self._access_expires_at:
            return self._access_token
        self._refresh_access(log=log)
        assert self._access_token
        return self._access_token

    def _list_messages(self, client: httpx.Client, access_token: str, *, top: int = 10) -> list[dict[str, Any]]:
        resp = client.get(
            f"{_GRAPH_BASE}/me/messages",
            params={
                "$top": top,
                "$orderby": "receivedDateTime desc",
                "$select": "subject,from,receivedDateTime,bodyPreview,body",
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json().get("value", [])

    def wait_for_otp(
        self,
        *,
        email: str,
        since: datetime,
        timeout_s: float,
        poll_interval_s: float,
        log: Callable[[str], None],
    ) -> str:
        deadline = time.monotonic() + max(timeout_s, 1.0)
        attempt = 0
        while True:
            attempt += 1
            access = self._ensure_access(log=log)
            with self._client() as client:
                messages = self._list_messages(client, access, top=8)
            # Gom hết ứng viên của lượt này rồi mới chọn, thay vì lấy cái đầu
            # tiên: Graph trả newest-first nên mail "login code" đến cùng giây
            # sẽ chen lên trước mail "verification code" ta thực sự cần.
            candidates: list[tuple[int, float, str]] = []
            for msg in messages:
                received = _parse_dt(msg.get("receivedDateTime"))
                if received is not None and since is not None and received < since:
                    continue
                sender = (msg.get("from") or {}).get("emailAddress", {}).get("address", "")
                subject = msg.get("subject") or ""
                body_obj = msg.get("body") or {}
                body = body_obj.get("content") or msg.get("bodyPreview") or ""
                code = _extract_otp(subject, body)
                if not code:
                    continue
                if not _is_chatgpt_otp_message(sender, subject, body=body, recipient=email):
                    continue
                kind = otp_kind(subject, body)
                # Ghi nhận loại TRƯỚC khi lọc mã đã dùng: mail verification vừa
                # dùng xong vẫn là bằng chứng flow đang chạy là flow verify email.
                if kind == "verification":
                    self._seen_verification_otp = True
                if code in self._used_otp_codes:
                    continue
                ts = received.timestamp() if received is not None else 0.0
                candidates.append((_OTP_KIND_RANK.get(kind, 1), -ts, code))
            # Lọc mã login SAU vòng lặp, không phải trong: mail login nằm trước
            # mail verification trong danh sách, lọc ngay thì cờ chưa kịp bật.
            # Đã thấy mail verification thì mã login là nhiễu của endpoint khác,
            # nộp vào validate chỉ tổ 401 rồi kéo theo 429 khi resend.
            if self._seen_verification_otp:
                candidates = [c for c in candidates if c[0] != _OTP_KIND_RANK["login"]]
            if candidates:
                candidates.sort()
                rank, _, code = candidates[0]
                self._used_otp_codes.add(code)
                kind = next(k for k, v in _OTP_KIND_RANK.items() if v == rank)
                log(f"[mail] OTP {code} ({kind}, attempt {attempt})")
                return code
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"OTP timeout after {timeout_s}s for {email}")
            time.sleep(min(poll_interval_s, remaining))

    def wait_for_verify_link(
        self,
        *,
        email: str,
        since: datetime,
        timeout_s: float,
        poll_interval_s: float,
        log: Callable[[str], None],
    ) -> str | None:
        deadline = time.monotonic() + max(timeout_s, 1.0)
        while True:
            access = self._ensure_access(log=log)
            with self._client() as client:
                messages = self._list_messages(client, access, top=8)
            for msg in messages:
                received = _parse_dt(msg.get("receivedDateTime"))
                if received is not None and received < since:
                    continue
                body_obj = msg.get("body") or {}
                body = body_obj.get("content") or msg.get("bodyPreview") or ""
                match = _VERIFY_LINK_RE.search(body)
                if match:
                    log(f"[mail] verify link found")
                    return match.group(0)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(poll_interval_s, remaining))

    def list_subjects(self, *, limit: int = 5) -> list[str]:
        log = lambda _m: None
        access = self._ensure_access(log=log)
        with self._client() as client:
            messages = self._list_messages(client, access, top=limit)
        return [str(m.get("subject") or "") for m in messages]
