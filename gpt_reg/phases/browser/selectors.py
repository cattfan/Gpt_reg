"""OpenAI auth UI selectors — single place to update.

Ưu tiên selector theo thuộc tính vì UI đổi ngôn ngữ theo locale/geoip; các cụm
text nằm trong `i18n.py`.
"""

from gpt_reg.phases.browser import i18n

EMAIL_INPUT = (
    'input[type="email"]',
    'input[name="email"]',
    'input[autocomplete="email"]',
)
PASSWORD_INPUT = (
    'input[type="password"]',
    'input[name="new-password"]',
    'input[autocomplete="new-password"]',
)
OTP_INPUT = (
    'input[name="code"]',
    'input[autocomplete="one-time-code"]',
    'input[inputmode="numeric"]',
)
SUBMIT = i18n.SUBMIT_BUTTONS
NAME_INPUT = ('input[name="name"]', 'input[autocomplete="name"]')
BIRTHDAY_INPUT = ('input[name="birthday"]', 'input[type="date"]')
