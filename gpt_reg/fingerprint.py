"""Canonical HTTP fingerprints and deterministic registration identities.

``curl_cffi`` owns browser identity headers and their ordering.  Values in this
module mirror a local capture from curl_cffi 0.15.0 so the navigator exposed to
Sentinel stays correlated with the TLS/header impersonation.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass


HardwareOption = tuple[int, int | None]


@dataclass(frozen=True)
class Profile:
    """One curl_cffi impersonation target and its correlated navigator data.

    The new navigator fields have derived defaults so existing callers that use
    the former six-argument constructor remain compatible.
    """

    name: str
    impersonate: str
    user_agent: str
    platform: str
    sec_ch_ua: str | None = None
    accept_language: str = "en-US,en;q=0.9"
    navigator_platform: str | None = None
    vendor: str | None = None
    has_user_agent_data: bool | None = None
    hardware_options: tuple[HardwareOption, ...] = ()

    def __post_init__(self) -> None:
        has_user_agent_data = self.has_user_agent_data
        if has_user_agent_data is None:
            has_user_agent_data = self.sec_ch_ua is not None
            object.__setattr__(self, "has_user_agent_data", has_user_agent_data)

        if self.navigator_platform is None:
            navigator_platform = {
                "Windows": "Win32",
                "Android": "Linux armv81",
                "iOS": "iPhone",
            }.get(self.platform, "MacIntel")
            object.__setattr__(self, "navigator_platform", navigator_platform)

        if self.vendor is None:
            if "Firefox/" in self.user_agent:
                vendor = ""
            elif "AppleWebKit/605.1.15" in self.user_agent:
                vendor = "Apple Computer, Inc."
            else:
                vendor = "Google Inc."
            object.__setattr__(self, "vendor", vendor)

        hardware_options = tuple(self.hardware_options)
        if not hardware_options:
            hardware_options = ((8, 8 if has_user_agent_data else None),)
            object.__setattr__(self, "hardware_options", hardware_options)
        if any(
            not isinstance(concurrency, int)
            or isinstance(concurrency, bool)
            or concurrency <= 0
            or (
                memory is not None
                and (
                    not isinstance(memory, int)
                    or isinstance(memory, bool)
                    or memory <= 0
                )
            )
            for concurrency, memory in hardware_options
        ):
            raise ValueError(f"Invalid hardware options for profile {self.name!r}")

    @property
    def sends_client_hints(self) -> bool:
        return self.sec_ch_ua is not None

    def context_headers(self, **extra: str) -> dict[str, str]:
        """Return only request-context headers; curl_cffi supplies identity."""
        return {key: value for key, value in extra.items() if value}


_WINDOWS_HARDWARE: tuple[HardwareOption, ...] = (
    (4, 4),
    (8, 8),
    (12, 8),
    (16, 8),
)
_MAC_CHROMIUM_HARDWARE: tuple[HardwareOption, ...] = (
    (8, 8),
    (10, 8),
    (12, 8),
)
_ANDROID_HARDWARE: tuple[HardwareOption, ...] = (
    (4, 4),
    (8, 4),
    (8, 8),
)
_MAC_NO_MEMORY: tuple[HardwareOption, ...] = (
    (4, None),
    (8, None),
    (10, None),
    (12, None),
)
_IOS_NO_MEMORY: tuple[HardwareOption, ...] = (
    (4, None),
    (6, None),
)

_WINDOWS_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{version} Safari/537.36"
)
_MAC_CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
)
_MAC_SAFARI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/{version} Safari/605.1.15"
)
_IOS_SAFARI_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/{version} Mobile/15E148 Safari/604.1"
)
_MAC_FIREFOX_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:{major}.0) "
    "Gecko/20100101 Firefox/{major}.0"
)


def _canonical_profile(
    name: str,
    user_agent: str,
    platform: str,
    *,
    sec_ch_ua: str | None = None,
    accept_language: str = "en-US,en;q=0.9",
    navigator_platform: str,
    vendor: str,
    has_user_agent_data: bool,
    hardware_options: tuple[HardwareOption, ...],
) -> Profile:
    return Profile(
        name=name,
        impersonate=name,
        user_agent=user_agent,
        platform=platform,
        sec_ch_ua=sec_ch_ua,
        accept_language=accept_language,
        navigator_platform=navigator_platform,
        vendor=vendor,
        has_user_agent_data=has_user_agent_data,
        hardware_options=hardware_options,
    )


# Canonical curl_cffi 0.15 targets. Order is part of the public registry contract.
PROFILES: tuple[Profile, ...] = (
    _canonical_profile(
        "edge99",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/99.0.4844.51 Safari/537.36 Edg/99.0.1150.30"
        ),
        "Windows",
        sec_ch_ua='" Not A;Brand";v="99", "Chromium";v="99", "Microsoft Edge";v="99"',
        navigator_platform="Win32",
        vendor="Google Inc.",
        has_user_agent_data=True,
        hardware_options=_WINDOWS_HARDWARE,
    ),
    _canonical_profile(
        "edge101",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/101.0.4951.64 Safari/537.36 Edg/101.0.1210.47"
        ),
        "Windows",
        sec_ch_ua='" Not A;Brand";v="99", "Chromium";v="101", "Microsoft Edge";v="101"',
        navigator_platform="Win32",
        vendor="Google Inc.",
        has_user_agent_data=True,
        hardware_options=_WINDOWS_HARDWARE,
    ),
    _canonical_profile(
        "chrome99",
        _WINDOWS_CHROME_UA.format(version="99.0.4844.51"),
        "Windows",
        sec_ch_ua='" Not A;Brand";v="99", "Chromium";v="99", "Google Chrome";v="99"',
        navigator_platform="Win32",
        vendor="Google Inc.",
        has_user_agent_data=True,
        hardware_options=_WINDOWS_HARDWARE,
    ),
    _canonical_profile(
        "chrome100",
        _WINDOWS_CHROME_UA.format(version="100.0.4896.75"),
        "Windows",
        sec_ch_ua='" Not A;Brand";v="99", "Chromium";v="100", "Google Chrome";v="100"',
        navigator_platform="Win32",
        vendor="Google Inc.",
        has_user_agent_data=True,
        hardware_options=_WINDOWS_HARDWARE,
    ),
    _canonical_profile(
        "chrome101",
        _WINDOWS_CHROME_UA.format(version="101.0.4951.67"),
        "Windows",
        sec_ch_ua='" Not A;Brand";v="99", "Chromium";v="101", "Google Chrome";v="101"',
        navigator_platform="Win32",
        vendor="Google Inc.",
        has_user_agent_data=True,
        hardware_options=_WINDOWS_HARDWARE,
    ),
    _canonical_profile(
        "chrome104",
        _WINDOWS_CHROME_UA.format(version="104.0.0.0"),
        "Windows",
        sec_ch_ua='"Chromium";v="104", " Not A;Brand";v="99", "Google Chrome";v="104"',
        navigator_platform="Win32",
        vendor="Google Inc.",
        has_user_agent_data=True,
        hardware_options=_WINDOWS_HARDWARE,
    ),
    _canonical_profile(
        "chrome107",
        _WINDOWS_CHROME_UA.format(version="107.0.0.0"),
        "Windows",
        sec_ch_ua='"Google Chrome";v="107", "Chromium";v="107", "Not=A?Brand";v="24"',
        navigator_platform="Win32",
        vendor="Google Inc.",
        has_user_agent_data=True,
        hardware_options=_WINDOWS_HARDWARE,
    ),
    _canonical_profile(
        "chrome110",
        _WINDOWS_CHROME_UA.format(version="110.0.0.0"),
        "Windows",
        sec_ch_ua='"Chromium";v="110", "Not A(Brand";v="24", "Google Chrome";v="110"',
        navigator_platform="Win32",
        vendor="Google Inc.",
        has_user_agent_data=True,
        hardware_options=_WINDOWS_HARDWARE,
    ),
    _canonical_profile(
        "chrome116",
        _WINDOWS_CHROME_UA.format(version="116.0.0.0"),
        "Windows",
        sec_ch_ua='"Chromium";v="116", "Not)A;Brand";v="24", "Google Chrome";v="116"',
        navigator_platform="Win32",
        vendor="Google Inc.",
        has_user_agent_data=True,
        hardware_options=_WINDOWS_HARDWARE,
    ),
    _canonical_profile(
        "chrome124",
        _MAC_CHROME_UA.format(major=124),
        "macOS",
        sec_ch_ua='"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        navigator_platform="MacIntel",
        vendor="Google Inc.",
        has_user_agent_data=True,
        hardware_options=_MAC_CHROMIUM_HARDWARE,
    ),
    _canonical_profile(
        "chrome131",
        _MAC_CHROME_UA.format(major=131),
        "macOS",
        sec_ch_ua='"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        navigator_platform="MacIntel",
        vendor="Google Inc.",
        has_user_agent_data=True,
        hardware_options=_MAC_CHROMIUM_HARDWARE,
    ),
    _canonical_profile(
        "chrome142",
        _MAC_CHROME_UA.format(major=142),
        "macOS",
        sec_ch_ua='"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
        navigator_platform="MacIntel",
        vendor="Google Inc.",
        has_user_agent_data=True,
        hardware_options=_MAC_CHROMIUM_HARDWARE,
    ),
    _canonical_profile(
        "chrome99_android",
        (
            "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/99.0.4844.58 Mobile Safari/537.36"
        ),
        "Android",
        sec_ch_ua='" Not A;Brand";v="99", "Chromium";v="99", "Google Chrome";v="99"',
        navigator_platform="Linux armv81",
        vendor="Google Inc.",
        has_user_agent_data=True,
        hardware_options=_ANDROID_HARDWARE,
    ),
    _canonical_profile(
        "safari153",
        _MAC_SAFARI_UA.format(version="15.3"),
        "macOS",
        accept_language="en-us",
        navigator_platform="MacIntel",
        vendor="Apple Computer, Inc.",
        has_user_agent_data=False,
        hardware_options=_MAC_NO_MEMORY,
    ),
    _canonical_profile(
        "safari180",
        _MAC_SAFARI_UA.format(version="18.0"),
        "macOS",
        navigator_platform="MacIntel",
        vendor="Apple Computer, Inc.",
        has_user_agent_data=False,
        hardware_options=_MAC_NO_MEMORY,
    ),
    _canonical_profile(
        "safari180_ios",
        _IOS_SAFARI_UA.format(version="18.0"),
        "iOS",
        navigator_platform="iPhone",
        vendor="Apple Computer, Inc.",
        has_user_agent_data=False,
        hardware_options=_IOS_NO_MEMORY,
    ),
    _canonical_profile(
        "safari184",
        _MAC_SAFARI_UA.format(version="18.4"),
        "macOS",
        navigator_platform="MacIntel",
        vendor="Apple Computer, Inc.",
        has_user_agent_data=False,
        hardware_options=_MAC_NO_MEMORY,
    ),
    _canonical_profile(
        "safari184_ios",
        _IOS_SAFARI_UA.format(version="18.4"),
        "iOS",
        navigator_platform="iPhone",
        vendor="Apple Computer, Inc.",
        has_user_agent_data=False,
        hardware_options=_IOS_NO_MEMORY,
    ),
    _canonical_profile(
        "safari2601",
        _MAC_SAFARI_UA.format(version="26.0.1"),
        "macOS",
        navigator_platform="MacIntel",
        vendor="Apple Computer, Inc.",
        has_user_agent_data=False,
        hardware_options=_MAC_NO_MEMORY,
    ),
    _canonical_profile(
        "firefox133",
        _MAC_FIREFOX_UA.format(major=133),
        "macOS",
        accept_language="en-US,en;q=0.5",
        navigator_platform="MacIntel",
        vendor="",
        has_user_agent_data=False,
        hardware_options=_MAC_NO_MEMORY,
    ),
    _canonical_profile(
        "firefox135",
        _MAC_FIREFOX_UA.format(major=135),
        "macOS",
        accept_language="en-US,en;q=0.5",
        navigator_platform="MacIntel",
        vendor="",
        has_user_agent_data=False,
        hardware_options=_MAC_NO_MEMORY,
    ),
    _canonical_profile(
        "firefox144",
        _MAC_FIREFOX_UA.format(major=144),
        "macOS",
        accept_language="en-US,en;q=0.5",
        navigator_platform="MacIntel",
        vendor="",
        has_user_agent_data=False,
        hardware_options=_MAC_NO_MEMORY,
    ),
    _canonical_profile(
        "firefox147",
        _MAC_FIREFOX_UA.format(major=147),
        "macOS",
        navigator_platform="MacIntel",
        vendor="",
        has_user_agent_data=False,
        hardware_options=_MAC_NO_MEMORY,
    ),
    _canonical_profile(
        "tor145",
        _MAC_FIREFOX_UA.format(major=128),
        "macOS",
        accept_language="en-US,en;q=0.5",
        navigator_platform="MacIntel",
        vendor="",
        has_user_agent_data=False,
        hardware_options=_MAC_NO_MEMORY,
    ),
)

DEFAULT_PROFILE = PROFILES[0]
_BY_NAME = {profile.name: profile for profile in PROFILES}

_SEED_RE = re.compile(r"[0-9a-fA-F]{32}\Z")
_HASH_DOMAIN = b"gpt-reg/fingerprint/v1"
_DEVICE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://gpt-reg.local/device/v1")
_CH_BRAND_RE = re.compile(r'"([^"]*)";v="([^"]*)"')

# Backward-compatible calls without a seed must be repeatable, never random.
DEFAULT_NAVIGATOR_SEED = "00000000000000000000000000000000"


def get_profile(name: str | None) -> Profile:
    """Resolve a canonical profile; nonempty unknown names fail fast."""
    if name is None or name == "":
        return DEFAULT_PROFILE
    if not isinstance(name, str):
        raise ValueError(f"Unknown fingerprint profile: {name!r}")
    try:
        return _BY_NAME[name]
    except KeyError:
        raise ValueError(f"Unknown fingerprint profile: {name!r}") from None


def validate_seed(seed: str) -> str:
    """Validate an exact 128-bit hexadecimal seed and normalize its case."""
    if not isinstance(seed, str) or _SEED_RE.fullmatch(seed) is None:
        raise ValueError("identity seed must contain exactly 32 hexadecimal characters")
    return seed.lower()


def new_seed() -> str:
    """Return a new 128-bit identity seed as lowercase hexadecimal."""
    return secrets.token_hex(16)


def _domain_digest(seed: str, purpose: str, *parts: str) -> bytes:
    normalized_seed = validate_seed(seed)
    digest = hashlib.sha256()
    for value in (_HASH_DOMAIN, purpose.encode("utf-8"), bytes.fromhex(normalized_seed)):
        digest.update(len(value).to_bytes(4, "big"))
        digest.update(value)
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.digest()


def identity_id(seed: str) -> str:
    """Return the stable, domain-separated 12-hex public identity id."""
    return _domain_digest(seed, "identity-id").hex()[:12]


def device_id_for_seed(seed: str, purpose: str = "device") -> str:
    """Derive a stable UUIDv5, separated by the caller-provided purpose."""
    normalized_seed = validate_seed(seed)
    if not isinstance(purpose, str) or not purpose.strip():
        raise ValueError("device id purpose must be a nonempty string")
    return str(uuid.uuid5(_DEVICE_NAMESPACE, f"{purpose}\0{normalized_seed}"))


def _profile_score(seed: str, profile: Profile) -> int:
    return int.from_bytes(
        _domain_digest(seed, "profile-rendezvous", profile.name),
        "big",
    )


def profile_for_seed(seed: str) -> Profile:
    """Select a profile with highest-random-weight (rendezvous) hashing."""
    normalized_seed = validate_seed(seed)
    return max(PROFILES, key=lambda profile: (_profile_score(normalized_seed, profile), profile.name))


def candidate_profiles(seed: str, preferred: str | None = None) -> tuple[Profile, ...]:
    """Return selected/preferred first, followed by a deterministic hash ranking."""
    normalized_seed = validate_seed(seed)
    ranked = sorted(
        PROFILES,
        key=lambda profile: (_profile_score(normalized_seed, profile), profile.name),
        reverse=True,
    )
    selected = ranked[0] if not preferred else get_profile(preferred)
    return (selected, *(profile for profile in ranked if profile is not selected))


def _navigator_languages(accept_language: str) -> list[str]:
    languages = [
        item.partition(";")[0].strip()
        for item in accept_language.split(",")
        if item.partition(";")[0].strip()
    ]
    return languages or ["en-US"]


def navigator_payload(profile: Profile, seed: str | None = None) -> dict[str, object]:
    """Build a deterministic navigator persona correlated with ``profile``.

    ``seed=None`` intentionally uses ``DEFAULT_NAVIGATOR_SEED`` for compatibility.
    It never introduces random navigator hardware into an existing call path.
    """
    normalized_seed = validate_seed(DEFAULT_NAVIGATOR_SEED if seed is None else seed)
    hardware_index = int.from_bytes(
        _domain_digest(normalized_seed, "navigator-hardware", profile.name),
        "big",
    ) % len(profile.hardware_options)
    hardware_concurrency, device_memory = profile.hardware_options[hardware_index]
    has_user_agent_data = bool(profile.has_user_agent_data)
    brands = [
        {"brand": brand, "version": version}
        for brand, version in _CH_BRAND_RE.findall(profile.sec_ch_ua or "")
    ] if has_user_agent_data else []
    languages = _navigator_languages(profile.accept_language)

    return {
        "user_agent": profile.user_agent,
        "platform": profile.navigator_platform,
        "vendor": profile.vendor,
        "language": languages[0],
        "languages": languages,
        "hardware_concurrency": hardware_concurrency,
        "device_memory": device_memory,
        "has_user_agent_data": has_user_agent_data,
        "sec_ch_ua_brands": brands,
        "sec_ch_ua_mobile": has_user_agent_data and profile.platform == "Android",
        "sec_ch_ua_platform": profile.platform if has_user_agent_data else None,
    }
