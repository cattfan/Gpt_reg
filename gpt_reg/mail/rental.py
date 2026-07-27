"""Shared contracts and failures for paid temporary-mail providers."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Callable, Protocol


class MailRentalError(RuntimeError):
    pass


class MailAuthError(MailRentalError):
    pass


class MailStockError(MailRentalError):
    pass


class MailBalanceError(MailRentalError):
    pass


class MailTimeoutError(MailRentalError):
    pass


class MailCancelledError(MailRentalError):
    pass


class MailUpstreamError(MailRentalError):
    pass


@dataclass(frozen=True)
class MailProduct:
    id: str
    name: str
    price: int
    stock: int
    description: str = ""


@dataclass(frozen=True)
class MailSourceStatus:
    configured: bool
    balance: int
    currency: str
    price: int
    stock: int
    affordable: int
    products: tuple[MailProduct, ...] = ()


@dataclass(frozen=True)
class MailRental:
    provider: str
    external_id: str
    base_email: str
    product_id: str | None = None
    expires_at: str | None = None
    balance_after_rent: int | None = None

    def updated(self, **fields) -> "MailRental":
        return replace(self, **fields)


class MailRentalProvider(Protocol):
    provider_id: str

    def status(self) -> MailSourceStatus: ...

    def rent(self, *, product_id: str | None = None) -> MailRental: ...

    def wait_for_otp(
        self,
        rental: MailRental,
        *,
        alias: str,
        timeout_s: float,
        poll_interval_s: float,
        should_cancel: Callable[[], bool] | None,
    ) -> str: ...

    def prepare_next(self, rental: MailRental) -> MailRental: ...

    def close(self, rental: MailRental, *, success: bool) -> None: ...


def poll_for_code(
    fetch: Callable[[], str | None],
    *,
    timeout_s: float,
    poll_interval_s: float,
    should_cancel: Callable[[], bool] | None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        if should_cancel is not None and should_cancel():
            raise MailCancelledError("mail rental cancelled")
        code = fetch()
        if code:
            return str(code).strip()
        if time.monotonic() >= deadline:
            raise MailTimeoutError("mail OTP timed out")
        sleep(max(0.01, poll_interval_s))
