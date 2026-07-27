from __future__ import annotations

from typing import Any


def playwright_proxy_dict(materialized: dict[str, str] | None) -> dict[str, str] | None:
    if not materialized:
        return None
    out: dict[str, str] = {"server": materialized["server"]}
    if materialized.get("username"):
        out["username"] = materialized["username"]
    if materialized.get("password"):
        out["password"] = materialized["password"]
    return out
