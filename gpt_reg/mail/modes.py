from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from gpt_reg.core.exceptions import OutlookComboError

_MIN_CHATGPT_PASSWORD_LEN = 12
_PASSWORD_PAD_POOL = "Aa1Bb2Cc3Dd4Ee5Ff6"
# BOM + zero-width — không phải whitespace nên str.strip() bỏ sót.
_INVISIBLE = "﻿​‌‍⁠\x00 \t\r\n"


@dataclass(frozen=True)
class ParsedLine:
    email: str
    raw: str


@dataclass(frozen=True)
class MailModeSpec:
    id: str
    label: str
    input_placeholder: str
    input_help: str
    parse_line: Callable[[str], ParsedLine]


def ensure_min_password(pw: str | None) -> str | None:
    if not pw or len(pw) >= _MIN_CHATGPT_PASSWORD_LEN:
        return pw
    need = _MIN_CHATGPT_PASSWORD_LEN - len(pw)
    return pw + _PASSWORD_PAD_POOL[:need]


def parse_outlook_combo(line: str) -> "OutlookCombo":
    return OutlookCombo.parse(line)


@dataclass
class OutlookCombo:
    email: str
    password: str
    refresh_token: str
    client_id: str

    @classmethod
    def parse(cls, combo: str) -> "OutlookCombo":
        # str.strip() không bỏ BOM/zero-width (không phải whitespace trong Python),
        # nên BOM từ file Notepad/PowerShell sẽ dính vào email và form auth từ chối.
        combo = combo.strip(_INVISIBLE)
        parts = combo.split("|")
        if len(parts) != 4:
            raise OutlookComboError(
                f"combo phải có 4 phần (email|password|refresh_token|client_id), nhận {len(parts)}"
            )
        email, password, refresh_token, client_id = (p.strip() for p in parts)
        if not email or "@" not in email:
            raise OutlookComboError(f"email không hợp lệ: {email!r}")
        if len(client_id) != 36 or client_id.count("-") != 4:
            raise OutlookComboError(f"client_id không phải UUID: {client_id!r}")
        return cls(
            email=email,
            password=password,
            refresh_token=refresh_token,
            client_id=client_id,
        )


def _parse_line_outlook(line: str) -> ParsedLine:
    combo = OutlookCombo.parse(line.strip())
    return ParsedLine(email=combo.email, raw=line.strip())


OUTLOOK_MODE = MailModeSpec(
    id="outlook",
    label="Outlook / Hotmail (Graph)",
    input_placeholder="email|password|refresh_token|client_id",
    input_help="Microsoft Graph combo — poll OTP từ inbox.",
    parse_line=_parse_line_outlook,
)

_REGISTRY: dict[str, MailModeSpec] = {
    OUTLOOK_MODE.id: OUTLOOK_MODE,
}


def get_registry() -> dict[str, MailModeSpec]:
    return dict(_REGISTRY)


def get_spec(mail_mode: str) -> MailModeSpec:
    try:
        return _REGISTRY[mail_mode]
    except KeyError as exc:
        raise KeyError(f"unknown mail mode: {mail_mode}") from exc


def serialize_for_api() -> list[dict[str, Any]]:
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "input_placeholder": spec.input_placeholder,
            "input_help": spec.input_help,
        }
        for spec in _REGISTRY.values()
    ]
