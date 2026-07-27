"""Bắt request thật của browser khi bấm "Tiếp tục với mật khẩu".

Luồng HTTP đang kẹt: `authorize/continue` trả `email_otp_verification`
(passwordless), gọi `user/register` lúc đó bị `invalid_auth_step`, và không có
tham số nào của `authorize/continue` chuyển sang chế độ mật khẩu được.

Browser thì làm được — nên ghi lại đúng request nó gửi.

Chạy tay:  .venv311\\Scripts\\python test\\probe_browser_capture.py <combo-file>
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

INTERESTING = ("/api/accounts/", "/backend-api/sentinel/")


async def _run(combo_line: str) -> int:
    from camoufox.async_api import AsyncCamoufox

    from gpt_reg.browser.driver import playwright_proxy_dict
    from gpt_reg.config import load_settings
    from gpt_reg.db import connect, migrate
    from gpt_reg.db.repositories import SettingsRepository
    from gpt_reg.mail.providers import build_request_from_combo
    from gpt_reg.phases.browser import register as reg
    from gpt_reg.phases.browser import screens as scr
    from gpt_reg.proxy.format import materialize_proxy
    from gpt_reg.proxy.pool import ProxyPool

    email, _password = build_request_from_combo(combo_line)
    settings = load_settings()
    conn = connect(settings.runtime_dir / "data.db")
    migrate(conn)
    pool = ProxyPool.from_multiline(SettingsRepository(conn).get("proxy.pool") or "")
    proxy_mat = pool.acquire()
    print(f"email: {email}")

    captured: list[dict] = []

    def on_request(request) -> None:
        url = request.url
        if not any(marker in url for marker in INTERESTING):
            return
        entry = {"method": request.method, "url": url.split("?")[0]}
        try:
            body = request.post_data
            if body:
                entry["body"] = body[:400]
        except Exception:
            pass
        try:
            headers = request.headers
            entry["sentinel"] = "openai-sentinel-token" in headers
        except Exception:
            pass
        captured.append(entry)

    cf = AsyncCamoufox(
        headless=True,
        persistent_context=True,
        user_data_dir=str(settings.profiles_dir / "capture_probe"),
        locale="en-US",
        geoip=bool(proxy_mat),
        proxy=playwright_proxy_dict(proxy_mat) or None,
    )
    async with cf as ctx:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.on("request", on_request)

        def log(msg: str) -> None:
            print("   ", msg)

        import uuid

        await reg.goto_chatgpt(page, artifact_dir=settings.artifacts_dir, log=log)
        await reg.bootstrap(
            page,
            email=email,
            device_id=str(uuid.uuid4()),
            logging_id=str(uuid.uuid4()),
            artifact_dir=settings.artifacts_dir,
            log=log,
        )

        # Chạy tới màn hình có nút password rồi bấm — ghi lại request phát sinh.
        for _ in range(40):
            screen = await scr.detect_screen(page)
            print(f"    screen={screen} url={(page.url or '').split('?')[0]}")
            if screen == scr.EMAIL_ENTRY:
                await reg.submit_email(page, email, log)
            elif screen == scr.CONTINUE:
                mark = len(captured)
                await reg.click_password_button(page, log)
                await asyncio.sleep(4)
                print("\n=== REQUEST SAU KHI BẤM NÚT PASSWORD ===")
                for entry in captured[mark:]:
                    print(json.dumps(entry, ensure_ascii=False, indent=2))
                print(f"\nurl sau khi bấm: {page.url}")
                break
            elif screen == scr.PASSWORD_CREATE:
                print("\n(đã ở màn tạo mật khẩu — không cần bấm)")
                break
            await asyncio.sleep(1.2)

        print("\n=== TOÀN BỘ REQUEST /api/accounts ĐÃ BẮT ===")
        for entry in captured:
            flag = " [sentinel]" if entry.get("sentinel") else ""
            print(f"  {entry['method']:5s} {entry['url']}{flag}")
            if entry.get("body"):
                print(f"        body: {entry['body'][:180]}")
    return 0


def main() -> int:
    combo_file = Path(sys.argv[1] if len(sys.argv) > 1 else "runtime/live_batch.txt")
    line = next(
        (r.strip() for r in combo_file.read_text(encoding="utf-8-sig").splitlines() if r.strip()), ""
    )
    if not line:
        print("combo file rỗng")
        return 1
    return asyncio.run(_run(line))


if __name__ == "__main__":
    raise SystemExit(main())
