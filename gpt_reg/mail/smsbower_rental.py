"""SMSBower temporary Gmail adapter."""

from __future__ import annotations

from typing import Callable, NoReturn

import httpx

from gpt_reg.mail.rental import (
    MailAuthError,
    MailBalanceError,
    MailProduct,
    MailRental,
    MailSourceStatus,
    MailStockError,
    MailTimeoutError,
    MailUpstreamError,
    poll_for_code,
)
from gpt_reg.sms.smsbower import SmsBowerClient, SmsBowerError

SERVICE = "dr"
DOMAIN = "gmail.com"
PRODUCT_ID = f"{SERVICE}:{DOMAIN}"


def _raise_mapped(exc: SmsBowerError) -> NoReturn:
    detail = str(exc)
    lower = detail.lower()
    if any(marker in lower for marker in ("bad_key", "no_key", "banned", "account_inactive")):
        raise MailAuthError("SMSBower authentication failed") from exc
    if "balance" in lower:
        raise MailBalanceError("SMSBower balance is insufficient") from exc
    if "no mails" in lower or "no such domain" in lower:
        raise MailStockError("SMSBower Gmail stock is unavailable") from exc
    if "timeout" in lower:
        raise MailTimeoutError("SMSBower request timed out") from exc
    raise MailUpstreamError(f"SMSBower request failed: {detail}") from exc


class SmsBowerMailRentalProvider:
    provider_id = "smsbower"

    def __init__(
        self,
        api_key: str,
        *,
        proxy_url: str | None = None,
        timeout: float = 25.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._client = SmsBowerClient(
            api_key,
            proxy_url=proxy_url,
            timeout=timeout,
            transport=transport,
        )
        self._sleep = sleep

    def status(self) -> MailSourceStatus:
        try:
            balance = self._client.get_balance()
            price, stock = self._client.get_mail_price_rests(
                service=SERVICE,
                domain=DOMAIN,
            )
        except SmsBowerError as exc:
            _raise_mapped(exc)
        balance_cents = max(0, round(balance * 100))
        price_cents = max(0, round(price * 100))
        affordable = balance_cents // price_cents if price_cents else 0
        product = MailProduct(
            id=PRODUCT_ID,
            name="Gmail OpenAI",
            price=price_cents,
            stock=max(0, stock),
            description="SMSBower temporary Gmail",
        )
        return MailSourceStatus(
            configured=True,
            balance=balance_cents,
            currency="USD",
            price=price_cents,
            stock=max(0, stock),
            affordable=affordable,
            products=(product,),
        )

    def rent(self, *, product_id: str | None = None) -> MailRental:
        if product_id not in (None, PRODUCT_ID):
            raise ValueError(f"unknown SMSBower mail product: {product_id!r}")
        try:
            activation = self._client.rent_mail(
                service=SERVICE,
                domain=DOMAIN,
                alias=False,
            )
        except SmsBowerError as exc:
            _raise_mapped(exc)
        return MailRental(
            provider=self.provider_id,
            external_id=activation.activation_id,
            base_email=activation.email,
            product_id=PRODUCT_ID,
        )

    def wait_for_otp(
        self,
        rental: MailRental,
        *,
        alias: str,
        timeout_s: float,
        poll_interval_s: float,
        should_cancel,
    ) -> str:
        del alias

        def fetch() -> str | None:
            try:
                return self._client.get_mail_code(rental.external_id)
            except SmsBowerError as exc:
                _raise_mapped(exc)

        kwargs = {}
        if self._sleep is not None:
            kwargs["sleep"] = self._sleep
        return poll_for_code(
            fetch,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            should_cancel=should_cancel,
            **kwargs,
        )

    def prepare_next(self, rental: MailRental) -> MailRental:
        try:
            self._client.set_mail_status(rental.external_id, 5)
        except SmsBowerError as exc:
            _raise_mapped(exc)
        return rental

    def close(self, rental: MailRental, *, success: bool) -> None:
        try:
            self._client.set_mail_status(rental.external_id, 3 if success else 2)
        except SmsBowerError as exc:
            _raise_mapped(exc)
