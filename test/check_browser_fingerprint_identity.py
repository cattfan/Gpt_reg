"""Kiem tra Camoufox preset that duoc materialize va validate fail-fast."""

from __future__ import annotations

import copy
import json


def _raises(call, error_type) -> bool:
    try:
        call()
    except error_type:
        return True
    except Exception:
        return False
    return False


def main() -> int:
    from gpt_reg.browser.fingerprint import (
        BrowserFingerprintError,
        browser_launch_identity,
        materialize_browser_fingerprint,
        parse_browser_fingerprint,
        serialize_browser_fingerprint,
    )

    failures: list[str] = []
    seed = "01" * 16
    one = materialize_browser_fingerprint(seed)
    two = materialize_browser_fingerprint(seed)
    try:
        materialize_browser_fingerprint(f"{19:032x}")
    except BrowserFingerprintError as exc:
        failures.append(f"preset trong bundle khong materialize duoc: {exc}")

    if one["preset_id"] != two["preset_id"]:
        failures.append("cung seed chon hai preset khac nhau")
    for key in ("fonts:spacing_seed", "audio:seed", "canvas:seed"):
        if one["config"].get(key) != two["config"].get(key):
            failures.append(f"noise seed {key} khong xac dinh")

    for key in (
        "schema",
        "seed_sha256",
        "preset_id",
        "preset",
        "bundle_sha256",
        "camoufox_version",
        "firefox_major",
        "os",
        "config",
    ):
        if key not in one:
            failures.append(f"payload thieu {key}")

    config = one.get("config") or {}
    for key in (
        "navigator.userAgent",
        "navigator.platform",
        "screen.width",
        "screen.height",
        "webGl:vendor",
        "webGl:renderer",
        "fonts",
        "voices",
        "window.history.length",
    ):
        if key not in config:
            failures.append(f"CAMOU_CONFIG thieu {key}")

    forbidden_prefixes = (
        "timezone",
        "locale:",
        "navigator.language",
        "headers.Accept-Language",
        "geolocation:",
        "webrtc:",
    )
    forbidden = [
        key for key in config
        if key == "addons" or any(key.startswith(prefix) for prefix in forbidden_prefixes)
    ]
    if forbidden:
        failures.append(f"config luu khoa runtime: {forbidden}")

    raw = serialize_browser_fingerprint(one)
    parsed = parse_browser_fingerprint(raw)
    if parsed != one:
        failures.append("serialize/parse khong round-trip")

    launch_config, launch_preset = browser_launch_identity(parsed)
    launch_config["canvas:seed"] = -1
    launch_preset.setdefault("navigator", {})["platform"] = "mutated"
    if one["config"]["canvas:seed"] == -1:
        failures.append("launch config khong deepcopy")
    if one["preset"]["navigator"]["platform"] == "mutated":
        failures.append("launch preset khong deepcopy")

    other_seed = "02" * 16
    if not _raises(
        lambda: parse_browser_fingerprint(one, expected_seed=other_seed),
        BrowserFingerprintError,
    ):
        failures.append("payload Browser cua seed khac khong fail-fast")
    if not _raises(
        lambda: browser_launch_identity(one, expected_seed=other_seed),
        BrowserFingerprintError,
    ):
        failures.append("launch Browser khong rang buoc payload voi seed")

    invalid_payloads = []
    bad = copy.deepcopy(one)
    bad["schema"] = 999
    invalid_payloads.append(bad)
    bad = copy.deepcopy(one)
    del bad["preset"]
    invalid_payloads.append(bad)
    bad = copy.deepcopy(one)
    bad["preset_id"] = "not-a-hash"
    invalid_payloads.append(bad)
    bad = copy.deepcopy(one)
    bad["bundle_sha256"] = "0" * 64
    invalid_payloads.append(bad)
    bad = copy.deepcopy(one)
    bad["config"]["webrtc:ipv4"] = "127.0.0.1"
    invalid_payloads.append(bad)
    for bad in invalid_payloads:
        if not _raises(lambda value=bad: parse_browser_fingerprint(value), BrowserFingerprintError):
            failures.append(f"payload hong khong fail-fast: {bad.keys()}")
    if not _raises(lambda: parse_browser_fingerprint("{hong-json"), BrowserFingerprintError):
        failures.append("JSON hong khong fail-fast")

    for line in failures:
        print(f"[fail] {line}")
    print(
        "[fail] browser fingerprint identity"
        if failures
        else "[ok] browser fingerprint identity"
    )
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
