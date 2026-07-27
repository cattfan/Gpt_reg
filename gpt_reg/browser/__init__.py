from gpt_reg.browser.artifacts import dump_html, screenshot
from gpt_reg.browser.challenges import assert_not_blocked, detect_block
from gpt_reg.browser.driver import playwright_proxy_dict
from gpt_reg.browser.hooks import HookRegistry, InitScriptHook
from gpt_reg.browser.nextauth import bootstrap_authorize_url

__all__ = [
    "screenshot",
    "dump_html",
    "assert_not_blocked",
    "detect_block",
    "playwright_proxy_dict",
    "HookRegistry",
    "InitScriptHook",
    "bootstrap_authorize_url",
]
