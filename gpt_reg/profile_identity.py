"""Deterministic, region-appropriate profile identities for registration jobs."""

from __future__ import annotations

import calendar
import hashlib
import random
from dataclasses import dataclass
from datetime import date
from typing import Literal

ProfileRegion = Literal["vi", "ko", "in"]


@dataclass(frozen=True)
class ProfileIdentity:
    region: ProfileRegion
    name: str
    birthdate: str


_VI_FAMILY = (
    "Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ",
    "Võ", "Đặng", "Bùi", "Đỗ", "Hồ", "Ngô", "Dương", "Lý",
)
_VI_MIDDLE = (
    "Minh", "Hoàng", "Quốc", "Thành", "Tuấn", "Đức", "Ngọc", "Hải",
    "Thuỳ", "Khánh", "Phương", "Thanh", "Anh", "Bảo", "Gia", "Đình",
)
_VI_GIVEN = (
    "An", "Bình", "Châu", "Dũng", "Giang", "Hà", "Hiếu", "Huy", "Khang",
    "Linh", "Mai", "Nam", "Nhi", "Phúc", "Quân", "Quỳnh", "Sơn", "Thảo",
    "Trang", "Tú", "Uyên", "Vy",
)

_KO_FAMILY = (
    "김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오",
)
_KO_GIVEN = (
    "민준", "서준", "도윤", "예준", "시우", "하준", "지호", "주원", "지우",
    "서연", "서윤", "지민", "하은", "수아", "예은", "다은", "유진", "채원",
)

_IN_GIVEN = (
    "Aarav", "Aditya", "Ananya", "Arjun", "Diya", "Ishaan", "Kavya",
    "Meera", "Neha", "Nikhil", "Priya", "Rahul", "Riya", "Rohan",
    "Saanvi", "Sanjay", "Shreya", "Vikram", "Vivaan", "Zoya",
)
_IN_FAMILY = (
    "Agarwal", "Bhat", "Chandra", "Desai", "Gupta", "Iyer", "Jain",
    "Kapoor", "Khan", "Kulkarni", "Mehta", "Menon", "Nair", "Patel",
    "Rao", "Reddy", "Shah", "Sharma", "Singh", "Verma",
)

_AGE_GROUPS: tuple[tuple[int, int, int], ...] = (
    (18, 24, 34),
    (25, 34, 44),
    (35, 45, 22),
)


def age_on(birthdate: date, on_date: date) -> int:
    """Return completed years at ``on_date``."""
    before_birthday = (on_date.month, on_date.day) < (birthdate.month, birthdate.day)
    return on_date.year - birthdate.year - int(before_birthday)


def _rng(region: ProfileRegion, seed: str) -> random.Random:
    if not isinstance(seed, str) or not seed:
        raise ValueError("profile seed must be a non-empty string")
    digest = hashlib.sha256(f"gpt-reg:profile:v1:{region}:{seed}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest, "big"))


def _birthdate(rng: random.Random, today: date) -> str:
    minimum, maximum, _weight = rng.choices(
        _AGE_GROUPS,
        weights=[group[2] for group in _AGE_GROUPS],
        k=1,
    )[0]
    target_age = rng.randint(minimum, maximum)
    month = rng.randint(1, 12)
    day = rng.randint(1, calendar.monthrange(today.year, month)[1])
    birthday_passed = (month, day) <= (today.month, today.day)
    year = today.year - target_age if birthday_passed else today.year - target_age - 1
    day = min(day, calendar.monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def _name(region: ProfileRegion, rng: random.Random) -> str:
    if region == "vi":
        return f"{rng.choice(_VI_FAMILY)} {rng.choice(_VI_MIDDLE)} {rng.choice(_VI_GIVEN)}"
    if region == "ko":
        return f"{rng.choice(_KO_FAMILY)}{rng.choice(_KO_GIVEN)}"
    return f"{rng.choice(_IN_GIVEN)} {rng.choice(_IN_FAMILY)}"


def generate_profile_identity(
    region: str,
    *,
    seed: str,
    today: date | None = None,
) -> ProfileIdentity:
    """Generate one stable identity for a supported profile region."""
    if region not in ("vi", "ko", "in"):
        raise ValueError(f"unsupported profile region: {region!r}")
    typed_region: ProfileRegion = region  # type: ignore[assignment]
    local_rng = _rng(typed_region, seed)
    reference_date = today or date.today()
    return ProfileIdentity(
        region=typed_region,
        name=_name(typed_region, local_rng),
        birthdate=_birthdate(local_rng, reference_date),
    )
