from __future__ import annotations

from typing import Any

from gpt_reg.core.contracts import BrowserHook


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: list[BrowserHook] = []

    def register(self, hook: BrowserHook) -> None:
        self._hooks.append(hook)

    def apply(self, context: Any, page: Any) -> None:
        for hook in self._hooks:
            hook.register(context, page)


class InitScriptHook:
    name = "init_script"

    def __init__(self, script: str):
        self._script = script

    def register(self, context: Any, page: Any) -> None:
        page.add_init_script(self._script)
