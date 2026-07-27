"""Offline contract checks for Gmail rental providers."""

from __future__ import annotations

from collections import Counter

import httpx


def _response(status: int, payload, *, request: httpx.Request) -> httpx.Response:
    if isinstance(payload, str):
        return httpx.Response(status, text=payload, request=request)
    return httpx.Response(status, json=payload, request=request)


def _check_alias(failures: list[str]) -> None:
    from gpt_reg.mail.alias import gmail_alias

    first = gmail_alias("base@gmail.com", seed="job-a", index=1)
    again = gmail_alias("base@gmail.com", seed="job-a", index=1)
    second = gmail_alias("base@gmail.com", seed="job-a", index=2)
    if first != again or first == second:
        failures.append("gmail alias is not deterministic/unique by index")
    if not first.startswith("base+") or not first.endswith("@gmail.com"):
        failures.append(f"gmail alias format is invalid: {first!r}")
    suffix = first.split("+", 1)[1].split("@", 1)[0]
    if len(suffix) != 6 or not suffix.isalnum() or suffix.lower() != suffix:
        failures.append(f"gmail alias suffix is invalid: {suffix!r}")


def _check_smsbower(failures: list[str]) -> None:
    from gpt_reg.mail.rental import MailAuthError
    from gpt_reg.mail.smsbower_rental import SmsBowerMailRentalProvider

    calls: Counter[str] = Counter()
    statuses: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls[path] += 1
        query = dict(request.url.params)
        if query.get("api_key") != "sms-secret":
            return _response(200, {"status": 0, "error": "BAD_KEY"}, request=request)
        if path.endswith("handler_api.php"):
            return _response(200, "ACCESS_BALANCE:2.00", request=request)
        if path.endswith("getPriceRests"):
            return _response(
                200,
                {"status": 1, "data": {"dr": {"gmail.com": {"price": 0.01, "count": 12}}}},
                request=request,
            )
        if path.endswith("getActivation"):
            if query.get("service") != "dr" or query.get("alias") != "0":
                return _response(200, {"status": 0, "error": "bad params"}, request=request)
            return _response(
                200,
                {"status": 1, "mail": "base@gmail.com", "mailId": 44},
                request=request,
            )
        if path.endswith("getCode"):
            if calls[path] == 1:
                return _response(
                    200,
                    {"status": 0, "error": "Code has not been received yet, please try again later"},
                    request=request,
                )
            return _response(200, {"status": 1, "code": "413902"}, request=request)
        if path.endswith("setStatus"):
            statuses.append(int(query["status"]))
            return _response(200, {"status": 1, "message": "Success"}, request=request)
        return _response(404, {"status": 0, "error": "missing"}, request=request)

    provider = SmsBowerMailRentalProvider(
        "sms-secret",
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    status = provider.status()
    if (status.balance, status.price, status.stock, status.affordable, status.currency) != (
        200,
        1,
        12,
        200,
        "USD",
    ):
        failures.append(f"SMSBower status normalization is wrong: {status!r}")
    rental = provider.rent()
    if rental.external_id != "44" or rental.base_email != "base@gmail.com":
        failures.append(f"SMSBower rent response is wrong: {rental!r}")
    code = provider.wait_for_otp(
        rental,
        alias="base+abc123@gmail.com",
        timeout_s=2,
        poll_interval_s=0.01,
        should_cancel=None,
    )
    if code != "413902":
        failures.append(f"SMSBower OTP is wrong: {code!r}")
    provider.prepare_next(rental)
    provider.close(rental, success=True)
    provider.close(rental, success=False)
    if statuses != [5, 3, 2]:
        failures.append(f"SMSBower activation statuses are wrong: {statuses!r}")

    def auth_handler(request: httpx.Request) -> httpx.Response:
        return _response(200, {"status": 0, "error": "BAD_KEY"}, request=request)

    bad = SmsBowerMailRentalProvider(
        "do-not-leak",
        transport=httpx.MockTransport(auth_handler),
    )
    try:
        bad.rent()
        failures.append("SMSBower BAD_KEY must raise MailAuthError")
    except MailAuthError as exc:
        if "do-not-leak" in str(exc):
            failures.append("SMSBower exception leaked API key")


def _check_accstack(failures: list[str]) -> None:
    from gpt_reg.mail.accstack import AccStackMailRentalProvider
    from gpt_reg.mail.rental import MailTimeoutError, MailUpstreamError

    calls: Counter[str] = Counter()
    seen_header: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls[path] += 1
        seen_header.append(request.headers.get("x-api-key", ""))
        if path.endswith("/me"):
            return _response(
                200,
                {"status": "success", "username": "fixture", "balance": 12500, "discount": 5},
                request=request,
            )
        if path.endswith("/products"):
            return _response(
                200,
                {
                    "status": "success",
                    "products": [
                        {"id": 5, "name": "Gmail OpenAI", "kind": "rent", "price": 50, "stock": 120, "min": 1, "max": 1, "description": "Receive OTP"},
                        {"id": 1, "name": "Hotmail Trusted", "kind": "buy", "price": 20, "stock": 30},
                        {"id": 8, "name": "Instagram mailbox", "kind": "rent", "price": 10, "stock": 20},
                    ],
                },
                request=request,
            )
        if path.endswith("/mail"):
            if request.url.params.get("product_id") != "5":
                return _response(400, {"detail": "bad product"}, request=request)
            return _response(
                200,
                {"status": "success", "order": "ORDER-1", "email": "base@gmail.com", "expires_at": "2026-07-27T21:00:00Z", "balance": 12450},
                request=request,
            )
        if path.endswith("/code"):
            if calls[path] == 1:
                return _response(200, {"status": "success", "rental_status": "waiting", "codes": [], "code": None}, request=request)
            return _response(200, {"status": "success", "rental_status": "received", "codes": ["992104"], "code": "992104"}, request=request)
        if path.endswith("/rerent"):
            return _response(200, {"status": "success", "order": "ORDER-1", "email": "base@gmail.com", "expires_at": "2026-07-27T21:15:00Z", "balance": 12400}, request=request)
        return _response(404, {"detail": "missing"}, request=request)

    provider = AccStackMailRentalProvider(
        "acc-secret",
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    status = provider.status()
    if (status.balance, status.price, status.stock, status.affordable) != (12500, 50, 120, 250):
        failures.append(f"AccStack status normalization is wrong: {status!r}")
    if len(status.products) != 1 or status.products[0].id != "5":
        failures.append(f"AccStack Gmail product filtering is wrong: {status.products!r}")
    rental = provider.rent(product_id="5")
    if rental.external_id != "ORDER-1" or rental.base_email != "base@gmail.com":
        failures.append(f"AccStack rental is wrong: {rental!r}")
    code = provider.wait_for_otp(
        rental,
        alias="base+abc123@gmail.com",
        timeout_s=2,
        poll_interval_s=0.01,
        should_cancel=None,
    )
    if code != "992104":
        failures.append(f"AccStack OTP is wrong: {code!r}")
    next_rental = provider.prepare_next(rental)
    if next_rental.expires_at != "2026-07-27T21:15:00Z":
        failures.append("AccStack rerent did not update expiry")
    if not seen_header or set(seen_header) != {"acc-secret"}:
        failures.append("AccStack did not use X-API-Key consistently")

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        return _response(502, {"detail": "upstream unavailable"}, request=request)

    upstream = AccStackMailRentalProvider(
        "hidden-key",
        transport=httpx.MockTransport(upstream_handler),
    )
    try:
        upstream.status()
        failures.append("AccStack HTTP 502 must raise MailUpstreamError")
    except MailUpstreamError as exc:
        if "hidden-key" in str(exc):
            failures.append("AccStack exception leaked API key")

    billable_calls = 0

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        nonlocal billable_calls
        billable_calls += 1
        raise httpx.ReadTimeout("fixture timeout", request=request)

    timeout_provider = AccStackMailRentalProvider(
        "hidden-key",
        transport=httpx.MockTransport(timeout_handler),
    )
    try:
        timeout_provider.rent(product_id="5")
        failures.append("AccStack billable timeout must raise MailTimeoutError")
    except MailTimeoutError:
        pass
    if billable_calls != 1:
        failures.append(f"AccStack billable call retried {billable_calls} times")


def main() -> int:
    failures: list[str] = []
    _check_alias(failures)
    _check_smsbower(failures)
    _check_accstack(failures)
    for line in failures:
        print(f"[fail] {line}")
    print("[fail] mail rental providers" if failures else "[ok] mail rental providers")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
