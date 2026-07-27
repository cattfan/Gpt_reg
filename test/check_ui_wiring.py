"""Kiểm tra contract tĩnh giữa FastAPI và bundle Vue production."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
STATIC_APP = ROOT / "gpt_reg" / "web" / "static" / "app"


def main() -> int:
    failures: list[str] = []
    index = (STATIC_APP / "index.html").read_text(encoding="utf-8")
    server = (ROOT / "gpt_reg" / "web" / "server.py").read_text(encoding="utf-8")
    app_vue = (FRONTEND / "src" / "App.vue").read_text(encoding="utf-8")
    sse = (FRONTEND / "src" / "services" / "sse.ts").read_text(encoding="utf-8")
    styles = (FRONTEND / "src" / "styles.css").read_text(encoding="utf-8")

    if "__AUTH_TOKEN__" in index or 'meta name="auth-token"' in index:
        failures.append("bundle index vẫn chứa auth-token")
    if 'STATIC / "app" / "index.html"' not in server:
        failures.append("FastAPI chưa phục vụ Vue production index")

    assets = re.findall(r'(?:src|href)="(/static/app/[^"]+)"', index)
    if not assets:
        failures.append("bundle index chưa nối JS/CSS đã build")
    for asset in assets:
        relative = asset.removeprefix("/static/app/")
        if not (STATIC_APP / relative).is_file():
            failures.append(f"bundle tham chiếu asset không tồn tại: {asset}")

    for view in ("RegistrationView", "CheckAccountsView", "SettingsView"):
        if view not in app_vue:
            failures.append(f"App shell thiếu view {view}")
    if sse.count("new EventSource") != 1:
        failures.append("SSE client phải chỉ có đúng một nơi tạo EventSource")
    for name in ("RegistrationView.vue", "CheckAccountsView.vue"):
        text = (FRONTEND / "src" / "views" / name).read_text(encoding="utf-8")
        if "new EventSource" in text:
            failures.append(f"{name} tự mở SSE thay vì dùng stream chung")

    if "@media (max-width: 760px)" not in styles or ".mobile-nav" not in styles:
        failures.append("CSS thiếu breakpoint/mobile navigation")
    if ".data-table {" not in styles or "min-width: 720px" not in styles:
        failures.append("bảng desktop thiếu min-width chống co chữ")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] ui wiring" if failures else "[ok] ui wiring")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
