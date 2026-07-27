"""Parse combo của tab Check acc: `mail|pass|2fa` hoặc `mail|pass|2fa|fullmail`.

- `mail`  : email tài khoản ChatGPT.
- `pass`  : mật khẩu TÀI KHOẢN ChatGPT (không phải mật khẩu hộp thư).
- `2fa`   : TOTP secret base32 (rỗng nếu account chưa bật 2FA).
- `fullmail` (tuỳ chọn): combo outlook đầy đủ `email|mailpass|refresh|clientid`.
  Chỉ cần khi account đòi verify email lúc đăng nhập — lúc đó phải đọc mã OTP qua
  Graph. `fullmail` tự chứa dấu `|`, nên **mọi field từ thứ 4 trở đi** được gộp
  lại thành nó.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class CheckComboError(ValueError):
    """Combo check sai định dạng."""

    def __init__(self, line_number: int, reason: str):
        self.line_number = line_number
        self.reason = reason
        super().__init__(f"dòng {line_number}: {reason}")


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_BASE32_RE = re.compile(r"^[A-Z2-7]+=*$")


@dataclass
class CheckCombo:
    email: str
    password: str
    totp_secret: str  # "" nếu không có 2FA
    full_combo: str   # "" nếu không kèm fullmail

    @property
    def has_totp(self) -> bool:
        return bool(self.totp_secret)

    @property
    def has_full_combo(self) -> bool:
        return bool(self.full_combo)

    @classmethod
    def parse(cls, line: str, *, line_number: int = 1) -> "CheckCombo":
        # BOM: Notepad/PowerShell 5.1 ghi UTF-8 kèm BOM, lọt vào email làm auth
        # từ chối. Đã gặp ở combo đăng ký, xử lý luôn ở đây.
        raw = (line or "").lstrip("﻿").strip()
        if not raw:
            raise CheckComboError(line_number, "dòng rỗng")
        parts = raw.split("|")
        if len(parts) < 3:
            raise CheckComboError(
                line_number,
                "cần tối thiểu mail|pass|2fa (2fa có thể để trống nhưng vẫn giữ dấu |)",
            )
        email = parts[0].strip()
        password = parts[1].strip()
        totp = parts[2].strip().replace(" ", "").upper()
        # Field 4 trở đi là fullmail (outlook combo tự chứa dấu |) → gộp lại.
        full_combo = "|".join(parts[3:]).strip() if len(parts) > 3 else ""

        if not _EMAIL_RE.match(email):
            raise CheckComboError(line_number, f"email không hợp lệ: {email!r}")
        if not password:
            raise CheckComboError(line_number, "thiếu mật khẩu")
        if totp and not _BASE32_RE.match(totp):
            raise CheckComboError(
                line_number, f"2fa secret không phải base32: {totp!r}"
            )
        return cls(email=email, password=password, totp_secret=totp, full_combo=full_combo)
