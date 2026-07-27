from __future__ import annotations

from typing import Callable

from gpt_reg.core.contracts import RegistrationPhase
from gpt_reg.core.exceptions import ConfigError
from gpt_reg.phases.browser import BrowserPhase, run_browser_phase
from gpt_reg.phases.http_reg import HttpRegPhase

_PHASES: dict[str, RegistrationPhase] = {
    "browser": BrowserPhase(),
    "http": HttpRegPhase(),
}


def get_phase(reg_mode: str) -> RegistrationPhase:
    try:
        return _PHASES[reg_mode]
    except KeyError as exc:
        raise ConfigError(f"unknown reg_mode: {reg_mode}") from exc


def register_phase(mode: str, phase: RegistrationPhase) -> None:
    _PHASES[mode] = phase


def available_modes() -> tuple[str, ...]:
    return tuple(_PHASES)
