"""Kiem tra identity seed va thu tu profile HTTP, khong can mang."""

from __future__ import annotations

import hashlib
import re
import uuid

import gpt_reg.fingerprint as fingerprint


EXPECTED_PROFILES = (
    "edge99",
    "edge101",
    "chrome99",
    "chrome100",
    "chrome101",
    "chrome104",
    "chrome107",
    "chrome110",
    "chrome116",
    "chrome124",
    "chrome131",
    "chrome142",
    "chrome99_android",
    "safari153",
    "safari180",
    "safari180_ios",
    "safari184",
    "safari184_ios",
    "safari2601",
    "firefox133",
    "firefox135",
    "firefox144",
    "firefox147",
    "tor145",
)

REQUIRED_API = (
    "candidate_profiles",
    "device_id_for_seed",
    "identity_id",
    "new_seed",
    "profile_for_seed",
    "validate_seed",
)

_HASH_DOMAIN = b"gpt-reg/fingerprint/v1"


def _reference_digest(seed: str, purpose: str, *parts: str) -> bytes:
    digest = hashlib.sha256()
    values = (_HASH_DOMAIN, purpose.encode("utf-8"), bytes.fromhex(seed.lower()))
    for value in values:
        digest.update(len(value).to_bytes(4, "big"))
        digest.update(value)
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.digest()


def _raises_value_error(call) -> bool:
    try:
        call()
    except ValueError:
        return True
    except Exception:
        return False
    return False


def main() -> int:
    failures: list[str] = []

    missing = [name for name in REQUIRED_API if not hasattr(fingerprint, name)]
    if missing:
        print(f"[fail] fingerprint thieu API identity: {', '.join(missing)}")
        return 1

    names = tuple(profile.name for profile in fingerprint.PROFILES)
    if names != EXPECTED_PROFILES:
        failures.append(f"registry khong dung 24 target canonical: {names}")
    impersonates = tuple(profile.impersonate for profile in fingerprint.PROFILES)
    if impersonates != EXPECTED_PROFILES:
        failures.append(f"registry dung alias thay vi target canonical: {impersonates}")

    if fingerprint.get_profile(None) is not fingerprint.DEFAULT_PROFILE:
        failures.append("get_profile(None) khong tra DEFAULT_PROFILE")
    if fingerprint.get_profile("") is not fingerprint.DEFAULT_PROFILE:
        failures.append("get_profile rong khong tra DEFAULT_PROFILE")

    validate_seed = fingerprint.validate_seed
    if validate_seed("ABCDEF0123456789ABCDEF0123456789") != "abcdef0123456789abcdef0123456789":
        failures.append("validate_seed khong chuan hoa hex hoa thanh chu thuong")

    invalid_seeds = (
        None,
        "",
        "0" * 31,
        "0" * 33,
        "g" * 32,
        "  " + "0" * 32,
        b"0" * 32,
    )
    for invalid in invalid_seeds:
        if not _raises_value_error(lambda value=invalid: validate_seed(value)):
            failures.append(f"seed khong hop le khong fail-fast: {invalid!r}")

    generated = (fingerprint.new_seed(), fingerprint.new_seed())
    if any(not re.fullmatch(r"[0-9a-f]{32}", value) for value in generated):
        failures.append(f"new_seed khong phai 32 hex chu thuong: {generated!r}")
    if generated[0] == generated[1]:
        failures.append("new_seed tra mot gia tri co dinh")

    seeds = tuple(f"{index:032x}" for index in range(1, 201))
    identity_ids = [fingerprint.identity_id(seed) for seed in seeds]
    if len(set(identity_ids)) != len(seeds):
        failures.append("200 seed khong tao ra 200 identity_id duy nhat")
    if any(not re.fullmatch(r"[0-9a-f]{12}", value) for value in identity_ids):
        failures.append("identity_id khong phai 12 hex chu thuong")
    reference_identity_ids = [
        _reference_digest(seed, "identity-id").hex()[:12]
        for seed in seeds
    ]
    if identity_ids != reference_identity_ids:
        failures.append("identity_id khong dung SHA-256 co domain separation")
    if identity_ids != [fingerprint.identity_id(seed.upper()) for seed in seeds]:
        failures.append("identity_id khong on dinh sau khi chuan hoa seed")

    device_ids = [fingerprint.device_id_for_seed(seed, purpose="sentinel") for seed in seeds]
    if len(set(device_ids)) != len(seeds):
        failures.append("200 seed khong tao ra 200 device UUID duy nhat")
    try:
        parsed_device_ids = [uuid.UUID(value) for value in device_ids]
    except (AttributeError, TypeError, ValueError):
        failures.append("device_id_for_seed khong tra UUID hop le")
    else:
        if any(value.version != 5 for value in parsed_device_ids):
            failures.append("device_id_for_seed khong dung UUIDv5")
    if device_ids != [fingerprint.device_id_for_seed(seed, purpose="sentinel") for seed in seeds]:
        failures.append("device_id_for_seed khong on dinh")
    if device_ids[0] == fingerprint.device_id_for_seed(seeds[0], purpose="account"):
        failures.append("purpose khong tach mien device UUID")

    selected_names: set[str] = set()
    expected_profile_names = set(EXPECTED_PROFILES)
    for seed in seeds:
        selected = fingerprint.profile_for_seed(seed)
        selected_names.add(selected.name)
        if fingerprint.profile_for_seed(seed) is not selected:
            failures.append(f"{seed}: profile_for_seed khong on dinh")

        candidates = fingerprint.candidate_profiles(seed)
        if candidates != fingerprint.candidate_profiles(seed):
            failures.append(f"{seed}: candidate_profiles khong on dinh")
        if not candidates or candidates[0] is not selected:
            failures.append(f"{seed}: candidate khong bat dau bang profile da chon")
        candidate_names = [profile.name for profile in candidates]
        if len(candidate_names) != len(EXPECTED_PROFILES) or set(candidate_names) != expected_profile_names:
            failures.append(f"{seed}: candidate khong phu moi profile dung mot lan")
        reference_order = tuple(
            sorted(
                fingerprint.PROFILES,
                key=lambda profile: (
                    int.from_bytes(
                        _reference_digest(seed, "profile-rendezvous", profile.name),
                        "big",
                    ),
                    profile.name,
                ),
                reverse=True,
            )
        )
        if selected is not reference_order[0] or candidates != reference_order:
            failures.append(f"{seed}: profile order khong dung rendezvous hash")

    if len(selected_names) <= 1:
        failures.append(f"phan phoi profile chi dung mot target: {selected_names}")

    preferred = EXPECTED_PROFILES[-1]
    preferred_candidates = fingerprint.candidate_profiles(seeds[0], preferred=preferred)
    if not preferred_candidates or preferred_candidates[0] is not fingerprint.get_profile(preferred):
        failures.append("preferred profile khong dung dau candidate order")
    if preferred_candidates != fingerprint.candidate_profiles(seeds[0], preferred=preferred):
        failures.append("preferred candidate order khong on dinh")
    preferred_names = [profile.name for profile in preferred_candidates]
    if len(preferred_names) != len(EXPECTED_PROFILES) or set(preferred_names) != expected_profile_names:
        failures.append("preferred candidate khong phu moi profile dung mot lan")
    if not _raises_value_error(
        lambda: fingerprint.candidate_profiles(seeds[0], preferred="khong-ton-tai")
    ):
        failures.append("preferred profile la khong fail-fast qua get_profile")
    if not _raises_value_error(lambda: fingerprint.get_profile("khong-ton-tai")):
        failures.append("get_profile khong fail-fast voi ten la")

    empty_preferred = fingerprint.candidate_profiles(seeds[0], preferred="")
    if not empty_preferred or empty_preferred[0] is not fingerprint.profile_for_seed(seeds[0]):
        failures.append("preferred rong phai giu profile rendezvous da chon")

    for line in failures:
        print(f"[fail] {line}")
    if failures:
        print("[fail] fingerprint identity")
    else:
        print("[ok] fingerprint identity: 200 identity duy nhat, 24 profile canonical")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
