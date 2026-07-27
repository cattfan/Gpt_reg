"""AccStack reseller API adapter for rented Gmail mailboxes."""

from __future__ import annotations

from typing import Any, Callable

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

BASE_URL = "https://accstack.io/api/v1"


class AccStackMailRentalProvider:
    provider_id = "accstack"

    def __init__(
        self,
        api_key: str,
        *,
        proxy_url: str | None = None,
        timeout: float = 25.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise MailAuthError("AccStack API key is missing")
        self._key = api_key.strip()
        self.proxy_url = proxy_url
        self._timeout = timeout
        self._transport = transport
        self._sleep = sleep

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            with httpx.Client(
                base_url=BASE_URL,
                headers={"X-API-Key": self._key, "Accept": "application/json"},
                timeout=self._timeout,
                proxy=self.proxy_url,
                verify=True,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = client.request(method, path, params=params, json=body)
        except httpx.TimeoutException as exc:
            raise MailTimeoutError(f"AccStack {path} timed out; request state is unknown") from exc
        except Exception as exc:
            raise MailUpstreamError(
                f"AccStack {path} network failure: {type(exc).__name__}"
            ) from exc

        payload: dict[str, Any] = {}
        try:
            decoded = response.json()
            if isinstance(decoded, dict):
                payload = decoded
        except Exception:
            payload = {}
        detail = str(payload.get("detail") or payload.get("error") or "request failed")[:160]
        if response.status_code in (401, 403):
            raise MailAuthError(f"AccStack authentication failed (HTTP {response.status_code})")
        if response.status_code == 400:
            lower = detail.lower()
            if "balance" in lower:
                raise MailBalanceError("AccStack balance is insufficient")
            if "stock" in lower or "quantity" in lower:
                raise MailStockError(f"AccStack stock error: {detail}")
            raise MailUpstreamError(f"AccStack request rejected: {detail}")
        if response.status_code >= 500:
            raise MailUpstreamError(f"AccStack upstream HTTP {response.status_code}: {detail}")
        if response.status_code >= 400:
            raise MailUpstreamError(f"AccStack HTTP {response.status_code}: {detail}")
        if payload.get("status") != "success":
            raise MailUpstreamError(f"AccStack returned non-success status: {detail}")
        return payload

    @staticmethod
    def _is_gmail_rental(raw: dict[str, Any]) -> bool:
        haystack = f"{raw.get('name') or ''} {raw.get('description') or ''}".lower()
        return str(raw.get("kind") or "").lower() == "rent" and "gmail" in haystack

    def status(self) -> MailSourceStatus:
        account = self._request("GET", "/me")
        catalog = self._request("GET", "/products")
        try:
            balance = max(0, int(account.get("balance") or 0))
        except (TypeError, ValueError) as exc:
            raise MailUpstreamError("AccStack /me returned invalid balance") from exc

        products: list[MailProduct] = []
        raw_products = catalog.get("products")
        if not isinstance(raw_products, list):
            raise MailUpstreamError("AccStack /products returned invalid products")
        for raw in raw_products:
            if not isinstance(raw, dict) or not self._is_gmail_rental(raw):
                continue
            try:
                price = max(0, int(raw.get("price") or 0))
                stock = max(0, int(raw.get("stock") or 0))
                product_id = str(raw["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if stock <= 0 or price <= 0:
                continue
            products.append(
                MailProduct(
                    id=product_id,
                    name=str(raw.get("name") or f"Gmail {product_id}"),
                    price=price,
                    stock=stock,
                    description=str(raw.get("description") or ""),
                )
            )
        products.sort(
            key=lambda product: (
                0
                if any(
                    marker in f"{product.name} {product.description}".casefold()
                    for marker in ("chatgpt", "openai")
                )
                else 1,
                product.price,
                product.name.casefold(),
                product.id,
            )
        )
        if not products:
            return MailSourceStatus(
                configured=True,
                balance=balance,
                currency="USD",
                price=0,
                stock=0,
                affordable=0,
                currency_divisor=1000,
                products=(),
            )
        primary = products[0]
        return MailSourceStatus(
            configured=True,
            balance=balance,
            currency="USD",
            price=primary.price,
            stock=primary.stock,
            affordable=balance // primary.price,
            currency_divisor=1000,
            products=tuple(products),
        )

    def rent(self, *, product_id: str | None = None) -> MailRental:
        if product_id is None or not str(product_id).strip():
            raise ValueError("AccStack product_id is required")
        payload = self._request(
            "GET",
            "/mail",
            params={"product_id": str(product_id)},
        )
        external_id = str(payload.get("order") or "").strip()
        email = str(payload.get("email") or "").strip()
        if not external_id or not email:
            raise MailUpstreamError("AccStack /mail omitted order/email")
        balance = payload.get("balance")
        try:
            balance_after = None if balance is None else int(balance)
        except (TypeError, ValueError):
            balance_after = None
        return MailRental(
            provider=self.provider_id,
            external_id=external_id,
            base_email=email,
            product_id=str(product_id),
            expires_at=str(payload.get("expires_at") or "") or None,
            balance_after_rent=balance_after,
        )

    def _code(self, rental: MailRental) -> str | None:
        payload = self._request(
            "GET",
            "/code",
            params={"order": rental.external_id},
        )
        rental_status = str(payload.get("rental_status") or "").lower()
        code = str(payload.get("code") or "").strip()
        if rental_status == "received" and code:
            return code
        if rental_status in ("waiting", ""):
            return None
        if rental_status == "refunded":
            raise MailUpstreamError("AccStack rental was refunded before OTP arrived")
        raise MailUpstreamError(f"AccStack returned unknown rental status: {rental_status}")

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
        kwargs = {}
        if self._sleep is not None:
            kwargs["sleep"] = self._sleep
        return poll_for_code(
            lambda: self._code(rental),
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            should_cancel=should_cancel,
            **kwargs,
        )

    def prepare_next(self, rental: MailRental) -> MailRental:
        payload = self._request(
            "GET",
            "/rerent",
            params={"order": rental.external_id},
        )
        balance = payload.get("balance")
        try:
            balance_after = rental.balance_after_rent if balance is None else int(balance)
        except (TypeError, ValueError):
            balance_after = rental.balance_after_rent
        return rental.updated(
            expires_at=str(payload.get("expires_at") or "") or rental.expires_at,
            balance_after_rent=balance_after,
        )

    def close(self, rental: MailRental, *, success: bool) -> None:
        del rental, success
