"""Tab "Check acc" — đăng nhập HTTP rồi đọc plan tài khoản ChatGPT.

Khác flow đăng ký: không tạo account, không session file. Dán combo dạng
`mail|pass|2fa` (hoặc `mail|pass|2fa|fullmail`), tool đăng nhập bằng mật khẩu +
TOTP rồi gọi `/backend-api/accounts/check` để lấy plan.
"""

from gpt_reg.checker.combo import CheckCombo, CheckComboError

__all__ = ["CheckCombo", "CheckComboError"]
