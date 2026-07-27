"""OpenAI Sentinel Token — explicit Python PoW mode.

Adapted from https://github.com/Regert888/gpt-outlook-register (sentinel.py).
Implements FNV-1a 32-bit PoW to solve challenges from /sentinel/req.

This is an operator-selected path. The primary path (sentinel_quickjs.py) runs OpenAI's
actual sdk.js in a Node subprocess and produces tokens that pass deep server-side
verification. This pure-Python path passes surface validation (200 OK) but OTP
dispatch may silent-drop. Enable it only through the explicit runtime switch.

Public API (matches sentinel_quickjs signature for drop-in):
    get_sentinel_token(session, device_id, flow) -> str
"""
from __future__ import annotations

import base64
import json
import logging
import random
import time
import uuid
from datetime import datetime, timezone

from gpt_reg.fingerprint import Profile, get_profile, navigator_payload, validate_seed

logger = logging.getLogger(__name__)

SENTINEL_REQ_URL = "https://sentinel.openai.com/backend-api/sentinel/req"
SENTINEL_REFERER = "https://sentinel.openai.com/backend-api/sentinel/frame.html"
# Chỉ dùng làm chuỗi config trong navigator giả lập, không tải thật — lấy từ
# quickjs để không bị lệch version.
from gpt_reg.sentinel.quickjs import SENTINEL_SDK_URL  # noqa: E402

# UA phải khớp profile đang gắn trên session (xem `_ua_of`). Hardcode UA ở đây
# từng gây mismatch giữa sentinel ↔ register cho cùng device_id: anti-bot OpenAI
# flag và trả 200 OK nhưng không gửi OTP.
def _profile_of(session):
    profile = getattr(session, "gpt_profile", None)
    if not isinstance(profile, Profile):
        raise RuntimeError("Sentinel session is missing fingerprint profile")
    return get_profile(profile.name)


def _seed_of(session) -> str:
    seed = getattr(session, "gpt_fingerprint_seed", None)
    if not isinstance(seed, str):
        raise RuntimeError("Sentinel session is missing fingerprint seed")
    return validate_seed(seed)

MAX_ATTEMPTS = 500_000
def _fnv1a_32(text: str) -> str:
    h = 2166136261
    for ch in text:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    h ^= h >> 16
    h = (h * 2246822507) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 3266489909) & 0xFFFFFFFF
    h ^= h >> 16
    return format(h & 0xFFFFFFFF, "08x")


def _b64_encode(data) -> str:
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _get_config(device_id: str, nav: dict[str, object]) -> list:
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)")
    perf_now = random.uniform(1000, 50000)
    time_origin = time.time() * 1000 - perf_now
    nav_prop = random.choice([
        "vendorSub", "productSub", "vendor", "maxTouchPoints",
        "scheduling", "userActivation", "doNotTrack", "geolocation",
        "connection", "plugins", "mimeTypes", "pdfViewerEnabled",
        "webkitTemporaryStorage", "webkitPersistentStorage",
        "hardwareConcurrency", "cookieEnabled", "credentials",
        "mediaDevices", "permissions", "locks", "ink",
    ])
    sid = str(uuid.uuid4())
    return [
        "1920x1080",
        date_str,
        4294705152,
        random.random(),
        nav["user_agent"],
        SENTINEL_SDK_URL,
        None,
        None,
        nav["language"],
        ",".join(str(value) for value in nav["languages"]),
        random.random(),
        f"{nav_prop}−undefined",
        random.choice(["location", "implementation", "URL", "documentURI", "compatMode"]),
        random.choice(["Object", "Function", "Array", "Number", "parseFloat", "undefined"]),
        perf_now,
        sid,
        "",
        nav["hardware_concurrency"],
        time_origin,
    ]


def _solve_pow(seed: str, difficulty: str, device_id: str, nav: dict[str, object]) -> str:
    """Run FNV-1a PoW until digest prefix <= difficulty."""
    config = _get_config(device_id, nav)
    start_time = time.time()
    for nonce in range(MAX_ATTEMPTS):
        config[3] = nonce
        config[9] = round((time.time() - start_time) * 1000)
        encoded = _b64_encode(config)
        digest = _fnv1a_32(seed + encoded)
        if digest[: len(difficulty)] <= difficulty:
            return "gAAAAAB" + encoded + "~S"
    raise RuntimeError("Sentinel proof-of-work exceeded the attempt limit")


def _generate_requirements_token(device_id: str, nav: dict[str, object]) -> str:
    config = _get_config(device_id, nav)
    config[3] = 1
    config[9] = round(random.uniform(5, 50))
    return "gAAAAAC" + _b64_encode(config)


def _fetch_challenge(session, device_id: str, flow: str, request_p: str) -> dict:
    """POST /sentinel/req → challenge JSON."""
    body = {"p": request_p, "id": device_id, "flow": flow}
    # Chỉ header theo ngữ cảnh — phần nhận dạng do curl_cffi gửi theo impersonate.
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Accept": "*/*",
        "Referer": SENTINEL_REFERER,
        "Origin": "https://sentinel.openai.com",
    }
    resp = session.post(
        SENTINEL_REQ_URL,
        data=json.dumps(body, separators=(",", ":")),
        headers=headers,
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Sentinel /req HTTP {resp.status_code}")
    payload = resp.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Sentinel challenge response is not an object")
    return payload


def get_sentinel_token(
    session,
    device_id: str,
    flow: str = "authorize_continue",
) -> str:
    """Build a Python PoW token using the session's canonical identity."""
    did = str(device_id or "").strip()
    if not did:
        raise RuntimeError("Sentinel device_id is required")
    nav = navigator_payload(_profile_of(session), _seed_of(session))
    req_p = _generate_requirements_token(did, nav)

    challenge = _fetch_challenge(session, did, flow, req_p)
    c_value = str(challenge.get("token") or "").strip()
    if not c_value:
        raise RuntimeError("Sentinel challenge token is empty")
    pow_data = challenge.get("proofofwork") or {}

    if pow_data.get("required") and pow_data.get("seed"):
        p_value = _solve_pow(
            seed=pow_data["seed"],
            difficulty=pow_data.get("difficulty", "0"),
            device_id=did,
            nav=nav,
        )
    else:
        p_value = req_p

    token = json.dumps(
        {"p": p_value, "t": "", "c": c_value, "id": did, "flow": flow},
        separators=(",", ":"),
    )
    logger.info("Sentinel token built (Python PoW, len=%d)", len(token))
    return token
