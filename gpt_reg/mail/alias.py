"""Stable Gmail plus aliases derived from a rental and alias index."""

from __future__ import annotations

import hashlib


def gmail_alias(base_email: str, *, seed: str, index: int) -> str:
    if not isinstance(base_email, str) or "@" not in base_email:
        raise ValueError("invalid Gmail address")
    if not isinstance(seed, str) or not seed:
        raise ValueError("alias seed must be a non-empty string")
    if not isinstance(index, int) or index < 1:
        raise ValueError("alias index must be a positive integer")
    local, domain = base_email.strip().rsplit("@", 1)
    if domain.lower() != "gmail.com" or not local:
        raise ValueError("rental mailbox must use gmail.com")
    root = local.split("+", 1)[0]
    suffix = hashlib.sha256(
        f"gpt-reg:gmail-alias:v1:{seed}:{index}".encode("utf-8")
    ).hexdigest()[:6]
    return f"{root}+{suffix}@gmail.com"
