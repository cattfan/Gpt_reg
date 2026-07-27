"""Smoke test a persisted Camoufox fingerprint across two real launches."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from camoufox.async_api import AsyncCamoufox

from gpt_reg.browser.fingerprint import (
    browser_launch_identity,
    materialize_browser_fingerprint,
)


SEED = "5f4dcc3b5aa765d61d8327deb882cf99"
SNAPSHOT_SCRIPT = """
() => ({
  ua: navigator.userAgent,
  platform: navigator.platform,
  cores: navigator.hardwareConcurrency,
  screen: [screen.width, screen.height, screen.availWidth, screen.availHeight],
  webgl: (() => {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl');
    const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
    return ext ? [
      gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),
      gl.getParameter(ext.UNMASKED_RENDERER_WEBGL),
    ] : null;
  })(),
})
"""


async def _launch_snapshot(payload: dict[str, Any], profile_dir: Path) -> dict[str, Any]:
    config, preset = browser_launch_identity(payload, expected_seed=SEED)
    camoufox = AsyncCamoufox(
        config=config,
        fingerprint_preset=preset,
        i_know_what_im_doing=True,
        headless=True,
        persistent_context=True,
        user_data_dir=str(profile_dir),
        geoip=False,
    )
    async with camoufox as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("about:blank")
        return await page.evaluate(SNAPSHOT_SCRIPT)


async def _run() -> int:
    payload = materialize_browser_fingerprint(SEED)
    config = payload["config"]
    expected = {
        "ua": config["navigator.userAgent"],
        "platform": config["navigator.platform"],
        "cores": config["navigator.hardwareConcurrency"],
        "screen": [
            config["screen.width"],
            config["screen.height"],
            config["screen.availWidth"],
            config["screen.availHeight"],
        ],
        "webgl": [config["webGl:vendor"], config["webGl:renderer"]],
    }
    with tempfile.TemporaryDirectory(prefix="gpt-reg-fingerprint-") as temp_dir:
        root = Path(temp_dir)
        first = await _launch_snapshot(payload, root / "first")
        second = await _launch_snapshot(payload, root / "second")

    failures: list[str] = []
    if first != second:
        failures.append(f"snapshots differ: first={first!r}, second={second!r}")
    if first != expected:
        failures.append(f"runtime does not match stored config: got={first!r}, expected={expected!r}")
    if first.get("webgl") is None:
        failures.append("WebGL fingerprint is unavailable")
    if "Firefox/" not in str(first.get("ua", "")):
        failures.append(f"unexpected browser UA: {first.get('ua')!r}")
    if not first.get("platform"):
        failures.append("navigator.platform is empty")
    if not isinstance(first.get("cores"), int) or first["cores"] <= 0:
        failures.append(f"invalid hardwareConcurrency: {first.get('cores')!r}")

    if failures:
        for failure in failures:
            print(f"[fail] {failure}")
        return len(failures)
    print("[ok] browser fingerprint stable")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
