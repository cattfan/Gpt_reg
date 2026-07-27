from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip("'\"")
    return values


def _parse_bool(val: str, *, default: bool) -> bool:
    if not val:
        return default
    return val.lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    root_dir: Path
    runtime_dir: Path
    browser_headless: bool = False
    browser_geoip: bool = True
    browser_locale: str = "vi-VN"
    web_port: int = 2023

    @property
    def profiles_dir(self) -> Path:
        return self.runtime_dir / "profiles"

    @property
    def sessions_dir(self) -> Path:
        return self.runtime_dir / "sessions"

    @property
    def artifacts_dir(self) -> Path:
        return self.runtime_dir / "artifacts"

    @property
    def outlook_state_dir(self) -> Path:
        return self.runtime_dir / "outlook_state"


def ensure_runtime_dirs(settings: Settings) -> None:
    for d in (
        settings.runtime_dir,
        settings.profiles_dir,
        settings.sessions_dir,
        settings.artifacts_dir,
        settings.outlook_state_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    root = Path(os.environ.get("GPT_REG_ROOT") or Path.cwd()).resolve()
    env_file = _load_env_file(root / ".env")
    runtime_raw = os.environ.get("RUNTIME_DIR") or env_file.get("RUNTIME_DIR") or "runtime"
    runtime_dir = Path(runtime_raw)
    if not runtime_dir.is_absolute():
        runtime_dir = root / runtime_dir
    port_raw = os.environ.get("WEB_PORT") or env_file.get("WEB_PORT") or "2023"
    try:
        web_port = int(port_raw)
    except ValueError:
        web_port = 2023
    headless = _parse_bool(
        os.environ.get("REG_HEADLESS") or env_file.get("REG_HEADLESS") or "",
        default=False,
    )
    geoip = _parse_bool(
        os.environ.get("BROWSER_GEOIP") or env_file.get("BROWSER_GEOIP") or "true",
        default=True,
    )
    # Locale quyết định ngôn ngữ UI của ChatGPT; selector text trong
    # `phases/browser/i18n.py` phủ vi-VN + en-US.
    locale = os.environ.get("BROWSER_LOCALE") or env_file.get("BROWSER_LOCALE") or "vi-VN"
    return Settings(
        root_dir=root,
        runtime_dir=runtime_dir.resolve(),
        browser_headless=headless,
        browser_geoip=geoip,
        browser_locale=locale,
        web_port=web_port,
    )
