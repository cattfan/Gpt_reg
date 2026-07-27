"""Dò vân tay nào qua được Cloudflare trên chatgpt.com.

KHÔNG chạy trong `setup.bat` — cần mạng + proxy và đụng vào endpoint thật.
Chạy tay khi HTTP reg bắt đầu bị CF 403 hàng loạt:

    .venv311\\Scripts\\python test\\probe_fingerprints.py

Mỗi profile gửi TLS + User-Agent + client hints ĐỒNG BỘ (Chrome thật gửi cùng
một phiên bản ở cả ba chỗ); gửi lệch nhau chính là tín hiệu anti-bot bắt được.
Đưa profile nào trả 200 lên đầu `fingerprint.PROFILES`.
"""

from __future__ import annotations

import time

from gpt_reg.config import load_settings
from gpt_reg.db import connect, migrate
from gpt_reg.db.repositories import SettingsRepository
from gpt_reg.fingerprint import PROFILES, Profile
from gpt_reg.proxy.pool import ProxyPool

TARGETS = (
    ("chatgpt/auth-login", "https://chatgpt.com/auth/login"),
    ("chatgpt/api-csrf", "https://chatgpt.com/api/auth/csrf"),
)


def _probe(profile: Profile, proxy: str | None) -> list[str]:
    from curl_cffi import requests as curl_requests

    out: list[str] = []
    for label, url in TARGETS:
        session = curl_requests.Session(impersonate=profile.impersonate)
        session.trust_env = False
        if proxy:
            session.proxies = {"https": proxy, "http": proxy}
        try:
            headers = {"Referer": "https://chatgpt.com/"}
            resp = session.get(url, headers=headers, timeout=25)
            body = (resp.text or "")[:400].lower()
            blocked = "just a moment" in body or "cf-chl" in body
            out.append(f"{label}={resp.status_code}{' CF-challenge' if blocked else ''}")
        except Exception as exc:
            out.append(f"{label}=ERR({type(exc).__name__})")
        finally:
            session.close()
    return out


def main() -> int:
    settings = load_settings()
    conn = connect(settings.runtime_dir / "data.db")
    migrate(conn)
    proxy = ProxyPool.from_multiline(SettingsRepository(conn).get("proxy.pool") or "").acquire_url()
    print(f"proxy: {'có' if proxy else 'không'}\n")
    print(f"{'profile':<22} {'impersonate':<14} kết quả")
    print("-" * 78)
    good = 0
    for profile in PROFILES:
        results = _probe(profile, proxy)
        ok = all("=200" in r for r in results)
        good += 1 if ok else 0
        mark = "OK " if ok else "   "
        print(f"{mark}{profile.name:<20} {profile.impersonate:<14} {'  '.join(results)}")
        time.sleep(1.0)
    print(f"\n{good}/{len(PROFILES)} profile qua được Cloudflare")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
