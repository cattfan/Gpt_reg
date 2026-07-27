"""Proxy line parsing — port pattern from privateGSH web/proxy_format.py."""
from __future__ import annotations

import random
import re
import string
from urllib.parse import quote

_SID_RE = re.compile(r"\{sid\}", re.IGNORECASE)
_PROXY_CRED_RE = re.compile(r"//[^/@\s]+@")


def sanitize_proxy_text(text: str) -> str:
    return _PROXY_CRED_RE.sub("//***@", text)


def _random_sid(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def materialize_proxy(line: str) -> dict[str, str]:
    raw = line.strip()
    if not raw:
        raise ValueError("empty proxy line")
    sid = _random_sid()
    if _SID_RE.search(raw):
        raw = _SID_RE.sub(sid, raw)

    if "://" in raw:
        scheme, rest = raw.split("://", 1)
        if "@" in rest:
            cred, host = rest.rsplit("@", 1)
            if ":" in cred:
                user, password = cred.split(":", 1)
            else:
                user, password = cred, ""
            host_part = host.split(":")
            if len(host_part) >= 2:
                server = f"{scheme}://{host_part[0]}:{host_part[1]}"
            else:
                server = f"{scheme}://{host}"
            return {
                "server": server,
                "username": user,
                "password": password,
            }
        return {"server": raw}

    if "@" in raw:
        cred, hostport = raw.rsplit("@", 1)
        if ":" in cred:
            user, password = cred.split(":", 1)
        else:
            user, password = cred, ""
        hp = hostport.split(":")
        if len(hp) < 2:
            raise ValueError(f"invalid proxy host:port: {hostport!r}")
        return {
            "server": f"http://{hp[0]}:{hp[1]}",
            "username": user,
            "password": password,
        }

    parts = raw.split(":")
    if len(parts) == 2:
        return {"server": f"http://{parts[0]}:{parts[1]}"}
    if len(parts) == 4:
        host, port, user, password = parts
        return {
            "server": f"http://{host}:{port}",
            "username": user,
            "password": password,
        }
    if len(parts) == 3:
        host, port, user = parts
        return {"server": f"http://{host}:{port}", "username": user, "password": ""}
    raise ValueError(f"unsupported proxy format: {line!r}")


def proxy_url_for_httpx(materialized: dict[str, str]) -> str | None:
    server = materialized.get("server", "")
    user = materialized.get("username") or ""
    password = materialized.get("password") or ""
    if not server:
        return None
    if user:
        if "://" in server:
            scheme, rest = server.split("://", 1)
            return f"{scheme}://{quote(user)}:{quote(password)}@{rest}"
    return server
