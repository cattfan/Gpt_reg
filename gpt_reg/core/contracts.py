from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Protocol, runtime_checkable

from gpt_reg.models import BrowserHandoff, SignupRequest


@runtime_checkable
class MailProvider(Protocol):
    def wait_for_otp(
        self,
        *,
        email: str,
        since: datetime,
        timeout_s: float,
        poll_interval_s: float,
        log: Callable[[str], None],
    ) -> str: ...

    def wait_for_verify_link(
        self,
        *,
        email: str,
        since: datetime,
        timeout_s: float,
        poll_interval_s: float,
        log: Callable[[str], None],
    ) -> str | None: ...


@runtime_checkable
class RegistrationPhase(Protocol):
    mode: str

    async def run(
        self,
        ctx: Any,
        request: SignupRequest,
        mail: MailProvider,
        *,
        log: Callable[[str], None],
    ) -> BrowserHandoff: ...


@runtime_checkable
class BrowserHook(Protocol):
    name: str

    def register(self, context: Any, page: Any) -> None: ...
