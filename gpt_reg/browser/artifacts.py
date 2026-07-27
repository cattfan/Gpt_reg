from __future__ import annotations

from datetime import datetime
from pathlib import Path

from playwright.async_api import Page


async def screenshot(page: Page, artifact_dir: Path, tag: str) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = artifact_dir / f"{tag}_{ts}.png"
    await page.screenshot(path=str(path), full_page=True)
    return path


async def dump_html(page: Page, artifact_dir: Path, tag: str) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = artifact_dir / f"{tag}_{ts}.html"
    path.write_text(await page.content(), encoding="utf-8")
    return path
