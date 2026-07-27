from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from gpt_reg.fingerprint import new_seed
from gpt_reg.user_agent_profile import CURL_IMPERSONATE_PRIMARY, WINDOWS_USER_AGENT

SignupCheckpoint = Literal[
    "password_confirmed",
    "account_created",
    "email_verified",
    "profile_complete",
    "session_ready",
]
SignupCheckpointCallback = Callable[[SignupCheckpoint], Awaitable[None] | None]
SignupIntent = Literal["register", "resume_existing", "probe_existing"]


class SignupRequest(BaseModel):
    email: str
    name: str = "ChatGPT User"
    birthdate: str = "2000-01-01"
    password: str | None = Field(default=None, repr=False)
    reg_mode: str = Field(default="browser", pattern="^(browser|http|pure_request)$")
    mail_provider: str = Field(default="outlook", pattern="^(outlook)$")
    outlook_combo: str | None = Field(default=None, repr=False)
    headless: bool = False
    keep_browser_open: bool = False
    proxy: str | None = None
    otp_timeout_seconds: float = Field(default=180.0, ge=10)
    otp_poll_interval_seconds: float = Field(default=2.0, ge=0.5)
    user_agent: str = Field(default=WINDOWS_USER_AGENT)
    impersonate: str = Field(default=CURL_IMPERSONATE_PRIMARY)
    fingerprint_seed: str = Field(default_factory=new_seed, repr=False)
    fingerprint_profile: str | None = None
    browser_fingerprint: dict[str, Any] | None = Field(default=None, repr=False)


class BrowserHandoff(BaseModel):
    cookies: list[dict[str, Any]] = Field(default_factory=list)
    authorize_url: str | None = None
    callback_url: str | None = None
    otp_seconds: float = 0.0
    authenticated_email: str | None = None
    access_token: str | None = Field(default=None, repr=False)
    user_id: str | None = Field(default=None, repr=False)
    user_agent: str | None = None
    impersonate: str | None = None
    fingerprint_profile: str | None = None
    # Mật khẩu THẬT của tài khoản khi phase tự chọn khác `request.password`
    # (đường đăng nhập lại dùng mật khẩu đã lưu, không phải mật khẩu hộp thư).
    account_password: str | None = Field(default=None, repr=False)


class SignupResult(BaseModel):
    ok: bool
    email: str
    password: str | None = Field(default=None, repr=False)
    error: str | None = None
    fallback_eligible: bool = False
    handoff: BrowserHandoff | None = None
    access_token: str | None = Field(default=None, repr=False)
    session_token: str | None = Field(default=None, repr=False)
    cookies: list[dict[str, Any]] = Field(default_factory=list)
    user_agent: str | None = None
    fingerprint_profile: str | None = None
    mfa_secret: str | None = Field(default=None, repr=False)
    mfa_activated: bool = False
    session_path: str | None = None
    exit_code: int = 0
    browser_seconds: float | None = None
    http_seconds: float | None = None
    mfa_seconds: float | None = None
