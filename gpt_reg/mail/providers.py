from __future__ import annotations

from pathlib import Path

from gpt_reg.core.contracts import MailProvider
from gpt_reg.mail.modes import OutlookCombo, ensure_min_password, parse_outlook_combo
from gpt_reg.mail.outlook import OutlookMailProvider


def build_provider(
    name: str,
    *,
    combo_line: str,
    state_dir: Path,
    proxy_url: str | None = None,
) -> MailProvider:
    if name != "outlook":
        raise ValueError(f"unsupported mail provider: {name}")
    combo = parse_outlook_combo(combo_line)
    return OutlookMailProvider(combo=combo, state_dir=state_dir, proxy_url=proxy_url)


def build_request_from_combo(
    combo_line: str,
    *,
    password_override: str | None = None,
) -> tuple[str, str | None]:
    combo = OutlookCombo.parse(combo_line)
    password = ensure_min_password(password_override or combo.password or None)
    return combo.email, password
