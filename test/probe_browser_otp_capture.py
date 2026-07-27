"""Bắt endpoint Browser dùng để resend/verify OTP passwordless.

Chạy: .venv311/Scripts/python test/probe_browser_otp_capture.py "<combo>"
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def _run(combo_raw: str) -> int:
    from camoufox.async_api import AsyncCamoufox

    from gpt_reg.browser.driver import playwright_proxy_dict
    from gpt_reg.config import ensure_runtime_dirs, load_settings
    from gpt_reg.db import connect, migrate
    from gpt_reg.db.repositories import SettingsRepository
    from gpt_reg.mail.modes import parse_outlook_combo
    from gpt_reg.mail.outlook import OutlookMailProvider
    from gpt_reg.phases.browser import otp as otp_mod
    from gpt_reg.phases.browser import register as reg
    from gpt_reg.phases.browser import screens as screens
    from gpt_reg.proxy.format import proxy_url_for_httpx
    from gpt_reg.proxy.pool import ProxyPool

    combo = parse_outlook_combo(combo_raw)
    settings = load_settings()
    ensure_runtime_dirs(settings)
    conn = connect(settings.runtime_dir / "data.db")
    migrate(conn)
    pool = ProxyPool.from_multiline(SettingsRepository(conn).get("proxy.pool") or "")
    proxy = pool.acquire()
    mail = OutlookMailProvider(
        combo=combo, state_dir=settings.outlook_state_dir,
        proxy_url=proxy_url_for_httpx(proxy),
    )
    captured: list[dict[str, object]] = []

    def capture(request) -> None:
        if "/api/accounts/" not in request.url:
            return
        entry: dict[str, object] = {
            "method": request.method,
            "path": request.url.split("auth.openai.com", 1)[-1].split("?", 1)[0],
        }
        if request.post_data:
            entry["body"] = request.post_data[:200]
        captured.append(entry)

    async with AsyncCamoufox(
        headless=True,
        persistent_context=True,
        user_data_dir=str(settings.profiles_dir / "otp_capture_probe"),
        locale="en-US",
        geoip=bool(proxy),
        proxy=playwright_proxy_dict(proxy) or None,
    ) as browser:
        page = browser.pages[0] if browser.pages else await browser.new_page()
        page.on("request", capture)
        await reg.goto_chatgpt(page, artifact_dir=settings.artifacts_dir, log=print)
        await reg.bootstrap(
            page,
            email=combo.email,
            device_id=str(uuid.uuid4()),
            logging_id=str(uuid.uuid4()),
            artifact_dir=settings.artifacts_dir,
            log=print,
        )
        for _ in range(30):
            screen = await screens.detect_screen(page)
            if screen == screens.OTP:
                break
            await asyncio.sleep(0.5)
        else:
            print(f"không tới OTP: screen={screen}, path={page.url.split('?')[0]}")
            return 1

        since = otp_mod.utc_now()
        mark = len(captured)
        await otp_mod.click_resend(page, print)
        code, _ = await otp_mod.poll_code(
            mail,
            email=combo.email,
            since=since,
            timeout_s=180,
            poll_interval_s=3,
            log=print,
            consumed=set(),
        )
        selector = await reg.wait_otp_form(page, timeout_s=15, log=print)
        await otp_mod.submit(page, code, print, selector=selector)
        await asyncio.sleep(8)
        print(f"after path={page.url.split('?')[0]} screen={await screens.detect_screen(page)}")
        for entry in captured[mark:]:
            print(json.dumps(entry, ensure_ascii=False))
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    return asyncio.run(_run(sys.argv[1].strip()))


if __name__ == "__main__":
    raise SystemExit(main())
