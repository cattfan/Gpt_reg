from __future__ import annotations

import random
from threading import Lock

from gpt_reg.proxy.format import materialize_proxy


class ProxyPool:
    def __init__(self, lines: list[str], *, rotation_mode: str = "round_robin"):
        self._raw = [ln.strip() for ln in lines if ln.strip()]
        self._mode = rotation_mode if rotation_mode in ("round_robin", "random") else "round_robin"
        self._idx = 0
        self._lock = Lock()

    @classmethod
    def from_multiline(cls, text: str | None, *, rotation_mode: str = "round_robin") -> "ProxyPool":
        lines = (text or "").splitlines()
        return cls(lines, rotation_mode=rotation_mode)

    def acquire(self) -> dict[str, str] | None:
        if not self._raw:
            return None
        with self._lock:
            if self._mode == "random":
                line = random.choice(self._raw)
            else:
                line = self._raw[self._idx % len(self._raw)]
                self._idx += 1
        return materialize_proxy(line)

    def acquire_url(self) -> str | None:
        from gpt_reg.proxy.format import proxy_url_for_httpx

        mat = self.acquire()
        return proxy_url_for_httpx(mat) if mat else None
