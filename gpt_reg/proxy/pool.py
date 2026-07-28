from __future__ import annotations

import secrets
from threading import Lock
from typing import Any

from gpt_reg.proxy.format import materialize_proxy


class ProxyPool:
    def __init__(self, lines: list[str], *, enabled: bool = True):
        raw = [line.strip() for line in lines if line.strip()]
        for line in raw:
            materialize_proxy(line)
        if enabled and not raw:
            raise ValueError("proxy is enabled but no proxies are configured")
        self._raw = raw if enabled else []
        self._enabled = enabled
        self._lock = Lock()

    @classmethod
    def from_multiline(
        cls,
        text: str | None,
        *,
        enabled: bool | None = None,
        rotation_mode: str | None = None,
    ) -> "ProxyPool":
        """Build a random pool from legacy text.

        ``rotation_mode`` remains accepted for callers from older releases, but
        selection is always random. An omitted ``enabled`` keeps empty CLI
        inputs as direct connections.
        """
        lines = (text or "").splitlines()
        active = bool([line for line in lines if line.strip()]) if enabled is None else enabled
        return cls(lines, enabled=active)

    @classmethod
    def from_records(
        cls,
        records: list[dict[str, Any]],
        *,
        enabled: bool,
    ) -> "ProxyPool":
        parsed: list[tuple[str, bool]] = []
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("proxy record must be an object")
            value = record.get("value")
            selected = record.get("selected")
            if not isinstance(value, str) or not value.strip():
                raise ValueError("proxy value must be a non-empty string")
            if not isinstance(selected, bool):
                raise ValueError("proxy selected must be bool")
            normalized = value.strip()
            materialize_proxy(normalized)
            if normalized in seen:
                raise ValueError(f"duplicate proxy value: {normalized!r}")
            seen.add(normalized)
            parsed.append((normalized, selected))

        selected_values = [value for value, selected in parsed if selected]
        return cls(selected_values, enabled=enabled)

    def acquire(self) -> dict[str, str] | None:
        if not self._raw:
            return None
        with self._lock:
            line = secrets.choice(self._raw)
        return materialize_proxy(line)

    def acquire_url(self) -> str | None:
        from gpt_reg.proxy.format import proxy_url_for_httpx

        mat = self.acquire()
        return proxy_url_for_httpx(mat) if mat else None
