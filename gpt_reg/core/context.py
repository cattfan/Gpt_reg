from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from gpt_reg.config import Settings
from gpt_reg.core.exceptions import JobCancelledError

if TYPE_CHECKING:
    from gpt_reg.db.repositories import SettingsRepository
    from gpt_reg.proxy.pool import ProxyPool


class RunContext:
    __slots__ = (
        "settings",
        "proxy_pool",
        "settings_repo",
        "artifact_dir",
        "on_checkpoint",
        "should_cancel",
        "on_account_created",
    )

    def __init__(
        self,
        *,
        settings: Settings,
        proxy_pool: "ProxyPool",
        settings_repo: "SettingsRepository | None" = None,
        artifact_dir: Path | None = None,
        on_checkpoint: Callable[..., Any] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        on_account_created: Callable[[str], None] | None = None,
    ):
        self.settings = settings
        self.proxy_pool = proxy_pool
        self.settings_repo = settings_repo
        self.artifact_dir = artifact_dir or settings.artifacts_dir
        self.on_checkpoint = on_checkpoint
        self.should_cancel = should_cancel
        self.on_account_created = on_account_created

    def account_created(self, password: str) -> None:
        """Báo rằng OpenAI đã chấp nhận đăng ký — account tồn tại từ giờ.

        Phải gọi NGAY khi `user/register` trả 200, trước cả bước OTP: nếu flow
        hỏng sau đó mà mật khẩu chưa được ghi thì account tồn tại nhưng không ai
        biết mật khẩu, và lần chạy sau sẽ đăng ký lại vô ích.
        """
        if self.on_account_created is not None:
            try:
                self.on_account_created(password)
            except Exception:
                pass  # ghi nhận thất bại không được làm hỏng flow đang chạy

    def raise_if_cancelled(self, step: str) -> None:
        """Điểm huỷ hợp tác — gọi ở đầu mỗi vòng lặp dài."""
        if self.should_cancel is not None and self.should_cancel():
            raise JobCancelledError(step=step)
