from __future__ import annotations


class GptRegError(Exception):
    """Base error."""


class ConfigError(GptRegError):
    pass


class MailError(GptRegError):
    pass


class OutlookComboError(MailError):
    pass


class OutlookProviderUnavailable(MailError):
    pass


class BrowserPhaseError(GptRegError):
    def __init__(self, message: str, *, step: str | None = None):
        super().__init__(message)
        self.step = step


class ChallengeBlockedError(GptRegError):
    def __init__(self, message: str, *, kind: str = "unknown"):
        super().__init__(message)
        self.kind = kind


class HttpRegError(GptRegError):
    """Đăng ký pure-HTTP thất bại (reg_mode="http")."""

    def __init__(self, message: str, *, step: str | None = None):
        super().__init__(message)
        self.step = step


class JobCancelledError(GptRegError):
    """User bấm Stop. Huỷ hợp tác — phase tự thoát ở điểm kiểm tra gần nhất."""

    def __init__(self, message: str = "đã huỷ theo yêu cầu", *, step: str | None = None):
        super().__init__(message)
        self.step = step
