"""Persistent Camoufox identities built from bundled real-world presets."""

from __future__ import annotations

import copy
import functools
import hashlib
import importlib.metadata
import json
import math
import re
from typing import Any

from camoufox import fingerprints, pkgman, utils as camoufox_utils
from camoufox.webgl import sample_webgl

from gpt_reg.fingerprint import validate_seed

__all__ = [
    "BrowserFingerprintError",
    "browser_launch_identity",
    "materialize_browser_fingerprint",
    "parse_browser_fingerprint",
    "serialize_browser_fingerprint",
]


SCHEMA_VERSION = 2
_OS_NAMES = ("macos", "windows", "linux")
_WEBGL_OS_NAMES = {"macos": "mac", "windows": "win", "linux": "lin"}
_PAYLOAD_KEYS = frozenset(
    {
        "schema",
        "seed_sha256",
        "preset_id",
        "preset",
        "bundle_sha256",
        "camoufox_version",
        "firefox_major",
        "os",
        "config",
    }
)
_HEX_16_RE = re.compile(r"[0-9a-f]{16}\Z")
_HEX_64_RE = re.compile(r"[0-9a-f]{64}\Z")
_FIREFOX_MAJOR_RE = re.compile(r"(\d+)(?:\.|\Z)")
_HASH_DOMAIN = b"gpt-reg/browser-fingerprint/v1"
_U32_MAX = 4_294_967_295


class BrowserFingerprintError(ValueError):
    """Stored or installed Camoufox fingerprint data is invalid."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise BrowserFingerprintError(f"fingerprint data is not canonical JSON: {exc}") from exc


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _seed_digest(seed: str, purpose: str, *parts: str) -> bytes:
    digest = hashlib.sha256()
    values = (_HASH_DOMAIN, purpose.encode("utf-8"), bytes.fromhex(seed))
    for value in values:
        digest.update(len(value).to_bytes(4, "big"))
        digest.update(value)
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.digest()


def _seed_int(seed: str, purpose: str, upper_bound: int) -> int:
    return int.from_bytes(_seed_digest(seed, purpose), "big") % upper_bound + 1


def _seed_commitment(seed: str) -> str:
    return _seed_digest(validate_seed(seed), "payload-binding").hex()


def _current_environment() -> tuple[str, int]:
    try:
        package_version = importlib.metadata.version("camoufox")
    except Exception as exc:
        raise BrowserFingerprintError("Camoufox package version is unavailable") from exc

    try:
        installed_version = pkgman.installed_verstr()
    except Exception as exc:
        raise BrowserFingerprintError("installed Camoufox Firefox version is unavailable") from exc
    if not isinstance(installed_version, str):
        raise BrowserFingerprintError("installed Camoufox Firefox version is invalid")
    match = _FIREFOX_MAJOR_RE.match(installed_version)
    if match is None:
        raise BrowserFingerprintError(
            f"installed Camoufox Firefox version is invalid: {installed_version!r}"
        )
    return package_version, int(match.group(1))


def _is_dynamic_path(path: tuple[str, ...]) -> bool:
    leaf = path[-1].lower()
    full = ".".join(path).lower()
    if leaf == "addons" or full.endswith(".addons"):
        return True
    if leaf.startswith("timezone"):
        return True
    if leaf == "locale" or leaf.startswith(("locale:", "locale.")):
        return True
    if full in {"language", "languages", "navigator.language", "navigator.languages"}:
        return True
    if (
        leaf == "accept-language"
        or leaf.endswith(".accept-language")
        or leaf.endswith(":accept-language")
    ):
        return True
    if leaf == "geolocation" or leaf.startswith(("geolocation:", "geolocation.")):
        return True
    if leaf == "webrtc" or leaf.startswith(("webrtc:", "webrtc.")):
        return True
    return False


def _sanitize_runtime(value: Any, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise BrowserFingerprintError("fingerprint object keys must be strings")
            item_path = (*path, key)
            if _is_dynamic_path(item_path):
                continue
            sanitized[key] = _sanitize_runtime(item, item_path)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_runtime(item, path) for item in value]
    return copy.deepcopy(value)


def _find_dynamic_path(value: Any, path: tuple[str, ...] = ()) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return ".".join((*path, repr(key)))
            item_path = (*path, key)
            if _is_dynamic_path(item_path):
                return ".".join(item_path)
            found = _find_dynamic_path(item, item_path)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_dynamic_path(item, (*path, f"[{index}]"))
            if found is not None:
                return found
    return None


def _assert_json_value(value: Any, path: str = "payload") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise BrowserFingerprintError(f"{path} contains a non-finite number")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise BrowserFingerprintError(f"{path} contains a non-string object key")
            _assert_json_value(item, f"{path}.{key}")
        return
    raise BrowserFingerprintError(f"{path} contains non-JSON value {type(value).__name__}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _platform_os(platform: Any) -> str | None:
    if platform == "MacIntel":
        return "macos"
    if platform == "Win32":
        return "windows"
    if isinstance(platform, str) and "linux" in platform.lower():
        return "linux"
    return None


def _validate_preset(preset: dict[str, Any], os_name: str) -> None:
    if not preset:
        raise BrowserFingerprintError("fingerprint preset must not be empty")
    for section in ("navigator", "screen", "webgl"):
        if not isinstance(preset.get(section), dict) or not preset[section]:
            raise BrowserFingerprintError(f"fingerprint preset has invalid {section}")

    navigator = preset["navigator"]
    if not isinstance(navigator.get("userAgent"), str) or not navigator["userAgent"]:
        raise BrowserFingerprintError("fingerprint preset has invalid navigator.userAgent")
    if _platform_os(navigator.get("platform")) != os_name:
        raise BrowserFingerprintError("fingerprint preset OS does not match payload OS")

    screen = preset["screen"]
    for key in ("width", "height"):
        value = screen.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise BrowserFingerprintError(f"fingerprint preset has invalid screen.{key}")

    webgl = preset["webgl"]
    for key in ("unmaskedVendor", "unmaskedRenderer"):
        if not isinstance(webgl.get(key), str) or not webgl[key]:
            raise BrowserFingerprintError(f"fingerprint preset has invalid webgl.{key}")

    dynamic_path = _find_dynamic_path(preset)
    if dynamic_path is not None:
        raise BrowserFingerprintError(f"fingerprint preset contains runtime key: {dynamic_path}")


def _validate_config(config: dict[str, Any], os_name: str) -> None:
    if not config:
        raise BrowserFingerprintError("Camoufox config must not be empty")
    dynamic_path = _find_dynamic_path(config)
    if dynamic_path is not None:
        raise BrowserFingerprintError(f"Camoufox config contains runtime key: {dynamic_path}")

    for key in ("fonts:spacing_seed", "audio:seed", "canvas:seed"):
        value = config.get(key)
        if not _is_int(value) or not 1 <= value <= _U32_MAX:
            raise BrowserFingerprintError(f"Camoufox config has invalid {key}")
    history_length = config.get("window.history.length")
    if not _is_int(history_length) or not 1 <= history_length <= 5:
        raise BrowserFingerprintError("Camoufox config has invalid window.history.length")

    for key in ("navigator.userAgent", "navigator.platform", "webGl:vendor", "webGl:renderer"):
        if not isinstance(config.get(key), str) or not config[key]:
            raise BrowserFingerprintError(f"Camoufox config has invalid {key}")
    if _platform_os(config["navigator.platform"]) != os_name:
        raise BrowserFingerprintError("Camoufox config OS does not match payload OS")

    for key in ("screen.width", "screen.height"):
        value = config.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise BrowserFingerprintError(f"Camoufox config has invalid {key}")
    for key in ("fonts", "voices"):
        if not isinstance(config.get(key), list):
            raise BrowserFingerprintError(f"Camoufox config has invalid {key}")


def _preset_identity(os_name: str, preset: dict[str, Any]) -> dict[str, Any]:
    return {"os": os_name, "preset": preset}


def _supports_webgl_pair(
    os_name: str,
    preset: dict[str, Any],
    cache: dict[tuple[str, str, str], bool],
) -> bool:
    webgl = preset["webgl"]
    key = (os_name, webgl["unmaskedVendor"], webgl["unmaskedRenderer"])
    if key in cache:
        return cache[key]
    try:
        sample_webgl(_WEBGL_OS_NAMES[os_name], key[1], key[2])
    except ValueError:
        supported = False
    except Exception as exc:
        raise BrowserFingerprintError("failed to validate Camoufox WebGL presets") from exc
    else:
        supported = True
    cache[key] = supported
    return supported


def _flatten_bundle(bundle: dict[str, Any]) -> list[tuple[str, dict[str, Any], str, str]]:
    grouped = bundle.get("presets")
    if not isinstance(grouped, dict):
        raise BrowserFingerprintError("Camoufox preset bundle has no presets object")

    candidates: list[tuple[str, dict[str, Any], str, str]] = []
    seen_ids: set[str] = set()
    webgl_support: dict[tuple[str, str, str], bool] = {}
    for os_name in _OS_NAMES:
        presets_for_os = grouped.get(os_name)
        if not isinstance(presets_for_os, list) or not presets_for_os:
            raise BrowserFingerprintError(f"Camoufox preset bundle has no {os_name} presets")
        os_candidates = 0
        for raw_preset in presets_for_os:
            if not isinstance(raw_preset, dict):
                raise BrowserFingerprintError("Camoufox preset bundle contains a non-object preset")
            preset = _sanitize_runtime(raw_preset)
            _assert_json_value(preset, "preset")
            _validate_preset(preset, os_name)
            if not _supports_webgl_pair(os_name, preset, webgl_support):
                continue
            identity_json = _canonical_json(_preset_identity(os_name, preset))
            preset_id = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()[:16]
            if preset_id in seen_ids:
                raise BrowserFingerprintError(f"duplicate Camoufox preset id: {preset_id}")
            seen_ids.add(preset_id)
            candidates.append((os_name, preset, preset_id, identity_json))
            os_candidates += 1
        if not os_candidates:
            raise BrowserFingerprintError(
                f"Camoufox preset bundle has no launchable {os_name} presets"
            )
    return candidates


@functools.lru_cache(maxsize=4)
def _installed_bundle_signature(
    firefox_major: int,
) -> tuple[str, frozenset[tuple[str, str, str]]]:
    """Return an immutable signature/index for the installed preset bundle."""
    try:
        bundle = fingerprints.load_presets(firefox_major)
    except Exception as exc:
        raise BrowserFingerprintError("failed to load Camoufox preset bundle") from exc
    if not isinstance(bundle, dict) or not bundle:
        raise BrowserFingerprintError("Camoufox preset bundle is missing")
    _assert_json_value(bundle, "bundle")
    candidates = _flatten_bundle(bundle)
    return (
        _sha256_json(bundle),
        frozenset(
            (os_name, preset_id, identity_json)
            for os_name, _, preset_id, identity_json in candidates
        ),
    )


def materialize_browser_fingerprint(seed: str) -> dict[str, Any]:
    """Select and fully materialize one bundled preset for ``seed``."""
    normalized_seed = validate_seed(seed)
    camoufox_version, firefox_major = _current_environment()
    try:
        bundle = fingerprints.load_presets(firefox_major)
    except Exception as exc:
        raise BrowserFingerprintError("failed to load Camoufox preset bundle") from exc
    if not isinstance(bundle, dict) or not bundle:
        raise BrowserFingerprintError("Camoufox preset bundle is missing")

    _assert_json_value(bundle, "bundle")
    bundle_sha256 = _sha256_json(bundle)
    candidates = _flatten_bundle(bundle)
    if not candidates:
        raise BrowserFingerprintError("Camoufox preset bundle contains no candidates")

    os_name, preset, preset_id, _identity_json = max(
        candidates,
        key=lambda candidate: (
            int.from_bytes(
                _seed_digest(normalized_seed, "preset-rendezvous", candidate[3]),
                "big",
            ),
            candidate[2],
        ),
    )
    config: dict[str, Any] = {
        "fonts:spacing_seed": _seed_int(normalized_seed, "fonts-spacing-seed", _U32_MAX),
        "audio:seed": _seed_int(normalized_seed, "audio-seed", _U32_MAX),
        "canvas:seed": _seed_int(normalized_seed, "canvas-seed", _U32_MAX),
        "window.history.length": _seed_int(normalized_seed, "history-length", 5),
    }
    try:
        camoufox_utils.launch_options(
            config=config,
            fingerprint_preset=copy.deepcopy(preset),
            headless=False,
            env={},
            i_know_what_im_doing=True,
        )
    except Exception as exc:
        raise BrowserFingerprintError("failed to materialize Camoufox preset") from exc

    config = _sanitize_runtime(config)
    _assert_json_value(config, "config")
    _validate_config(config, os_name)
    payload = {
        "schema": SCHEMA_VERSION,
        "seed_sha256": _seed_commitment(normalized_seed),
        "preset_id": preset_id,
        "preset": copy.deepcopy(preset),
        "bundle_sha256": bundle_sha256,
        "camoufox_version": camoufox_version,
        "firefox_major": firefox_major,
        "os": os_name,
        "config": config,
    }
    return parse_browser_fingerprint(payload)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BrowserFingerprintError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise BrowserFingerprintError(f"invalid JSON number: {value}")


def parse_browser_fingerprint(
    raw: str | dict[str, Any],
    *,
    expected_seed: str | None = None,
) -> dict[str, Any]:
    """Parse and validate stored fingerprint data without selecting a fallback."""
    if isinstance(raw, str):
        try:
            decoded = json.loads(
                raw,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_json_constant,
            )
        except BrowserFingerprintError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise BrowserFingerprintError(f"invalid browser fingerprint JSON: {exc}") from exc
    elif isinstance(raw, dict):
        try:
            decoded = copy.deepcopy(raw)
        except Exception as exc:
            raise BrowserFingerprintError("browser fingerprint object cannot be copied") from exc
    else:
        raise BrowserFingerprintError("browser fingerprint must be a JSON string or object")

    if not isinstance(decoded, dict):
        raise BrowserFingerprintError("browser fingerprint payload must be an object")
    try:
        _assert_json_value(decoded)
    except (RecursionError, BrowserFingerprintError):
        raise
    if set(decoded) != _PAYLOAD_KEYS:
        missing = sorted(_PAYLOAD_KEYS - set(decoded))
        unknown = sorted(set(decoded) - _PAYLOAD_KEYS)
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unknown:
            details.append(f"unknown={unknown}")
        raise BrowserFingerprintError(f"invalid browser fingerprint fields: {', '.join(details)}")

    if not _is_int(decoded["schema"]) or decoded["schema"] != SCHEMA_VERSION:
        raise BrowserFingerprintError(
            f"unsupported browser fingerprint schema: {decoded['schema']!r}"
        )
    if (
        not isinstance(decoded["seed_sha256"], str)
        or _HEX_64_RE.fullmatch(decoded["seed_sha256"]) is None
    ):
        raise BrowserFingerprintError("browser fingerprint seed_sha256 must be 64 lowercase hex")
    if (
        expected_seed is not None
        and decoded["seed_sha256"] != _seed_commitment(expected_seed)
    ):
        raise BrowserFingerprintError("browser fingerprint does not match the requested seed")
    if (
        not isinstance(decoded["preset_id"], str)
        or _HEX_16_RE.fullmatch(decoded["preset_id"]) is None
    ):
        raise BrowserFingerprintError("browser fingerprint preset_id must be 16 lowercase hex")
    if (
        not isinstance(decoded["bundle_sha256"], str)
        or _HEX_64_RE.fullmatch(decoded["bundle_sha256"]) is None
    ):
        raise BrowserFingerprintError("browser fingerprint bundle_sha256 must be 64 lowercase hex")
    if not isinstance(decoded["camoufox_version"], str) or not decoded["camoufox_version"]:
        raise BrowserFingerprintError("browser fingerprint camoufox_version is invalid")
    if not _is_int(decoded["firefox_major"]) or decoded["firefox_major"] <= 0:
        raise BrowserFingerprintError("browser fingerprint firefox_major is invalid")
    if decoded["os"] not in _OS_NAMES:
        raise BrowserFingerprintError(f"browser fingerprint OS is invalid: {decoded['os']!r}")
    if not isinstance(decoded["preset"], dict):
        raise BrowserFingerprintError("browser fingerprint preset must be an object")
    if not isinstance(decoded["config"], dict):
        raise BrowserFingerprintError("browser fingerprint config must be an object")

    camoufox_version, firefox_major = _current_environment()
    if decoded["camoufox_version"] != camoufox_version:
        raise BrowserFingerprintError(
            "browser fingerprint Camoufox version does not match the installed package"
        )
    if decoded["firefox_major"] != firefox_major:
        raise BrowserFingerprintError(
            "browser fingerprint Firefox major does not match the installed runtime"
        )

    bundle_sha256, installed_presets = _installed_bundle_signature(firefox_major)
    if decoded["bundle_sha256"] != bundle_sha256:
        raise BrowserFingerprintError(
            "browser fingerprint bundle does not match the installed package"
        )

    _validate_preset(decoded["preset"], decoded["os"])
    _validate_config(decoded["config"], decoded["os"])
    expected_preset_id = _sha256_json(
        _preset_identity(decoded["os"], decoded["preset"])
    )[:16]
    if decoded["preset_id"] != expected_preset_id:
        raise BrowserFingerprintError("browser fingerprint preset_id does not match its preset")
    stored_identity = _canonical_json(
        _preset_identity(decoded["os"], decoded["preset"])
    )
    if (decoded["os"], decoded["preset_id"], stored_identity) not in installed_presets:
        raise BrowserFingerprintError(
            "browser fingerprint preset is not in the installed bundle"
        )

    _canonical_json(decoded)
    return copy.deepcopy(decoded)


def serialize_browser_fingerprint(data: str | dict[str, Any]) -> str:
    """Validate and serialize a fingerprint using compact canonical JSON."""
    return _canonical_json(parse_browser_fingerprint(data))


def browser_launch_identity(
    parsed: str | dict[str, Any],
    *,
    expected_seed: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return isolated config and preset copies for one Camoufox launch."""
    payload = parse_browser_fingerprint(parsed, expected_seed=expected_seed)
    return copy.deepcopy(payload["config"]), copy.deepcopy(payload["preset"])
