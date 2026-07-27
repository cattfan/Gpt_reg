"""Coordinate one paid mailbox across deterministic Gmail aliases."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from gpt_reg.fingerprint import new_seed, profile_for_seed
from gpt_reg.mail.alias import gmail_alias
from gpt_reg.mail.rental import MailRental, MailRentalProvider
from gpt_reg.models import SignupResult
from gpt_reg.profile_identity import generate_profile_identity


def _is_expired(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= datetime.now(timezone.utc)


class RentalMailboxProvider:
    """Bridge a rented mailbox to the registration phase MailProvider contract."""

    def __init__(
        self,
        provider: MailRentalProvider,
        rental: MailRental,
        alias: str,
        should_cancel: Callable[[], bool] | None,
    ) -> None:
        self._provider = provider
        self._rental = rental
        self._alias = alias
        self._should_cancel = should_cancel
        self.proxy_url = getattr(provider, "proxy_url", None)

    def wait_for_otp(
        self,
        *,
        email: str,
        since,
        timeout_s: float,
        poll_interval_s: float,
        log,
    ) -> str:
        del email, since, log
        return self._provider.wait_for_otp(
            self._rental,
            alias=self._alias,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            should_cancel=self._should_cancel,
        )

    def wait_for_verify_link(
        self,
        *,
        email: str,
        since,
        timeout_s: float,
        poll_interval_s: float,
        log,
    ) -> None:
        del email, since, timeout_s, poll_interval_s, log
        return None


class RentalCoordinator:
    def run_rental(
        self,
        *,
        rental_id: str,
        provider: MailRentalProvider,
        rentals_repo,
        jobs_repo,
        source: str,
        product_id: str | None,
        alias_limit: int,
        profile_region: str,
        reg_mode: str,
        execute: Callable[[dict[str, Any], RentalMailboxProvider], SignupResult],
        should_cancel: Callable[[], bool],
        balance_before: int | None,
    ) -> list[str]:
        if source not in ("gmail_smsbower", "gmail_accstack"):
            raise ValueError(f"unsupported rental source: {source!r}")
        if not isinstance(alias_limit, int) or alias_limit < 1:
            raise ValueError("alias_limit must be a positive integer")

        created_at = time.time()
        try:
            rental = provider.rent(product_id=product_id)
        except Exception as exc:
            rentals_repo.create(
                {
                    "id": rental_id,
                    "provider": provider.provider_id,
                    "external_id": "",
                    "base_email": "",
                    "product_id": product_id,
                    "status": "error",
                    "balance_before": balance_before,
                    "alias_count": 0,
                    "created_at": created_at,
                    "finished_at": time.time(),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            raise

        rentals_repo.create(
            {
                "id": rental_id,
                "provider": rental.provider,
                "external_id": rental.external_id,
                "base_email": rental.base_email,
                "product_id": rental.product_id,
                "status": "active",
                "expires_at": rental.expires_at,
                "balance_before": balance_before,
                "balance_after_rent": rental.balance_after_rent,
                "alias_count": 0,
                "created_at": created_at,
            }
        )

        job_ids: list[str] = []
        for alias_index in range(1, alias_limit + 1):
            if should_cancel():
                provider.close(rental, success=False)
                rentals_repo.update(
                    rental_id,
                    status="cancelled",
                    finished_at=time.time(),
                    error="stopped",
                )
                return job_ids
            if _is_expired(rental.expires_at):
                provider.close(rental, success=False)
                rentals_repo.update(
                    rental_id,
                    status="expired",
                    finished_at=time.time(),
                    error="rental expired",
                )
                return job_ids

            job_id = uuid.uuid4().hex
            email = gmail_alias(rental.base_email, seed=rental_id, index=alias_index)
            profile = generate_profile_identity(profile_region, seed=job_id)
            fingerprint_seed = new_seed()
            row = {
                "id": job_id,
                "email": email,
                "combo": email,
                "mail_mode": source,
                "reg_mode": reg_mode,
                "status": "queued",
                "error": None,
                "password": None,
                "session_path": None,
                "fingerprint_seed": fingerprint_seed,
                "fingerprint_profile": profile_for_seed(fingerprint_seed).name,
                "fingerprint_data": None,
                "rental_id": rental_id,
                "source_email": rental.base_email,
                "alias_index": alias_index,
                "profile_region": profile.region,
                "profile_name": profile.name,
                "birthdate": profile.birthdate,
                "created_at": time.time(),
                "started_at": None,
                "finished_at": None,
            }
            jobs_repo.create(row)
            job_ids.append(job_id)
            mailbox = RentalMailboxProvider(provider, rental, email, should_cancel)

            try:
                result = execute(row, mailbox)
            except Exception as exc:
                jobs_repo.update(
                    job_id,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                    finished_at=time.time(),
                )
                provider.close(rental, success=False)
                rentals_repo.update(
                    rental_id,
                    status="error",
                    alias_count=alias_index,
                    finished_at=time.time(),
                    error=f"{type(exc).__name__}: {exc}",
                )
                return job_ids

            outcome = result.outcome or (
                "success"
                if result.ok
                else "cancelled"
                if result.error == "cancelled"
                else "failed"
            )
            job_status = "success" if outcome == "success" and result.ok else "error"
            if outcome == "cancelled":
                job_status = "cancelled"
            jobs_repo.update(
                job_id,
                status=job_status,
                error=None if job_status == "success" else (result.error or outcome),
                password=result.password,
                session_path=result.session_path,
                mfa_activated=1 if result.mfa_activated else 0,
                browser_seconds=result.browser_seconds,
                http_seconds=result.http_seconds,
                mfa_seconds=result.mfa_seconds,
                finished_at=time.time(),
            )
            rentals_repo.update(rental_id, alias_count=alias_index)

            if outcome == "account_exists":
                provider.close(rental, success=True)
                rentals_repo.update(
                    rental_id,
                    status="account_exists",
                    finished_at=time.time(),
                    error="account already exists",
                )
                return job_ids
            if outcome == "cancelled":
                provider.close(rental, success=False)
                rentals_repo.update(
                    rental_id,
                    status="cancelled",
                    finished_at=time.time(),
                    error="stopped",
                )
                return job_ids
            if outcome != "success" or not result.ok:
                provider.close(rental, success=False)
                rentals_repo.update(
                    rental_id,
                    status="error",
                    finished_at=time.time(),
                    error=result.error or "registration failed",
                )
                return job_ids
            if alias_index >= alias_limit:
                provider.close(rental, success=True)
                rentals_repo.update(
                    rental_id,
                    status="limit_reached",
                    finished_at=time.time(),
                )
                return job_ids

            rental = provider.prepare_next(rental)
            rentals_repo.update(
                rental_id,
                expires_at=rental.expires_at,
                balance_after_rent=rental.balance_after_rent,
            )

        return job_ids
