"""Live one-mailbox/one-alias registration smoke test.

This command is billable. It reads provider credentials and proxy state from
the runtime SQLite database and never prints mailbox, order, OTP, or API keys.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid

from gpt_reg.config import load_settings
from gpt_reg.db import connect, migrate
from gpt_reg.db.repositories import (
    JobRepository,
    MailRentalRepository,
    ProxyRepository,
    SettingsRepository,
)
from gpt_reg.mail.accstack import AccStackMailRentalProvider
from gpt_reg.mail.rental import MailRentalError
from gpt_reg.mail.smsbower_rental import SmsBowerMailRentalProvider
from gpt_reg.proxy.pool import ProxyPool
from gpt_reg.web.jobs.reg_manager import RegJobManager
from gpt_reg.web.jobs.rental_coordinator import RentalCoordinator

PROVIDERS = ("smsbower", "accstack")


def _enabled(value: str | None) -> bool:
    return str(value or "false").lower() in ("1", "true", "yes", "on")


def _provider(provider_id: str, api_key: str, proxy_url: str | None):
    if provider_id == "smsbower":
        return SmsBowerMailRentalProvider(api_key, proxy_url=proxy_url)
    return AccStackMailRentalProvider(api_key, proxy_url=proxy_url)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=PROVIDERS, required=True)
    parser.add_argument("--mode", choices=("http", "browser"), default="http")
    parser.add_argument("--product-id")
    parser.add_argument("--fallback", action="store_true")
    parser.add_argument("--confirm-charge", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _args()
    if not args.confirm_charge:
        print("[dry-run] add --confirm-charge to run one paid rental")
        return 2

    started = time.monotonic()
    runtime = load_settings()
    conn = connect(runtime.runtime_dir / "data.db")
    migrate(conn)
    settings_repo = SettingsRepository(conn)
    jobs_repo = JobRepository(conn)
    rentals_repo = MailRentalRepository(conn)
    proxy_repo = ProxyRepository(conn)

    source = f"gmail_{args.provider}"
    key_name = (
        "sms.smsbower.api_key" if args.provider == "smsbower" else "accstack.api_key"
    )
    api_key = settings_repo.get(key_name)
    if not api_key:
        print(f"[error] {source} is not configured in SQLite")
        conn.close()
        return 2

    try:
        pool = ProxyPool.from_records(
            proxy_repo.list_all(),
            enabled=_enabled(settings_repo.get("proxy.enabled")),
        )
        proxy_url = pool.acquire_url()
        provider = _provider(args.provider, api_key, proxy_url)
        before = provider.status()
        products = {product.id: product for product in before.products}
        product_id = args.product_id or (next(iter(products)) if len(products) == 1 else None)
        if product_id is None:
            print("[error] multiple products available; pass --product-id")
            return 2
        product = products.get(str(product_id))
        if product is None:
            print("[error] selected product is unavailable")
            return 2
        if product.stock < 1 or before.balance < product.price:
            print("[error] insufficient stock or balance for one rental")
            return 2

        manager = RegJobManager()
        rental_id = uuid.uuid4().hex
        coordinator = RentalCoordinator()
        job_ids = coordinator.run_rental(
            rental_id=rental_id,
            provider=provider,
            rentals_repo=rentals_repo,
            jobs_repo=jobs_repo,
            source=source,
            product_id=str(product_id),
            alias_limit=1,
            aliases_enabled=False,
            profile_region="vi",
            reg_mode=args.mode,
            execute=lambda row, mailbox: manager._run_one(
                jobs_repo,
                row,
                headless=True,
                with_2fa=False,
                reg_mode=args.mode,
                fallback_enabled=args.fallback,
                mail_override=mailbox,
            ),
            should_cancel=lambda: False,
            balance_before=before.balance,
        )
        after = _provider(args.provider, api_key, proxy_url).status()
    except (MailRentalError, ValueError) as exc:
        print(f"[error] {args.provider}: {type(exc).__name__}: {exc}")
        return 1
    except Exception as exc:
        print(f"[error] {args.provider}: {type(exc).__name__}")
        return 1
    finally:
        conn.close()

    # Reopen after the worker path has committed, without retaining a closed connection.
    verify_conn = connect(runtime.runtime_dir / "data.db")
    try:
        rental_row = MailRentalRepository(verify_conn).get(rental_id) or {}
        job_row = JobRepository(verify_conn).get(job_ids[-1]) if job_ids else None
    finally:
        verify_conn.close()

    delta = before.balance - after.balance
    print(
        "[result] "
        f"provider={args.provider} mode={args.mode} fallback={args.fallback} "
        f"rented=1 aliases={len(job_ids)} rental_status={rental_row.get('status')} "
        f"job_status={(job_row or {}).get('status')} balance_before={before.balance} "
        f"balance_after={after.balance} delta={delta} cents "
        f"elapsed={time.monotonic() - started:.1f}s"
    )
    return 0 if job_row and job_row.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
