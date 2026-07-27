"""Khoá tính đúng của vân tay.

Hai điều được kiểm:

1. **Không tự dựng header nhận dạng.** curl_cffi khi impersonate đã gửi trọn bộ
   `User-Agent` / `sec-ch-ua` / `Sec-Fetch-*` / `Accept-Encoding` đúng giá trị và
   đúng thứ tự. Ghi đè bằng dict là phá vân tay — đo thật trên chatgpt.com:
   chrome131 + header mặc định = 200, cùng chrome131 + header tự dựng = 403.

2. **Bảng profile khớp với header curl_cffi thật sự gửi.** Sentinel đọc UA từ
   bảng này để nạp vào `navigator.userAgent` cho sdk.js; lệch với UA trên dây là
   deep validation trượt và OTP bị silent-drop. Test bắt request thật qua socket
   local (không cần mạng) để so.
"""

from __future__ import annotations

import re
import socket
import sys
import tempfile
import threading
from pathlib import Path

from gpt_reg.fingerprint import DEFAULT_NAVIGATOR_SEED, PROFILES, Profile, get_profile, navigator_payload

_FIXED_SEED = "0123456789abcdef0123456789abcdef"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_IDENTITY_HEADERS = (
    "user-agent",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "accept-language",
    "accept-encoding",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "sec-fetch-user",
    "upgrade-insecure-requests",
    "priority",
    "connection",
)


def _capture_headers(impersonate: str) -> dict[str, str]:
    """Bắt header curl_cffi thật sự gửi, qua server HTTP local."""
    from curl_cffi import requests

    port_box: list[int] = []
    captured: list[str] = []
    ready = threading.Event()

    def _serve() -> None:
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port_box.append(srv.getsockname()[1])
        ready.set()
        try:
            conn, _ = srv.accept()
            conn.settimeout(3)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            captured.append(data.decode("latin-1"))
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            conn.close()
        except Exception:
            pass
        finally:
            srv.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    if not ready.wait(timeout=5) or not port_box:
        return {}

    session = requests.Session(impersonate=impersonate)
    session.trust_env = False
    session.proxies = {"http": "", "https": ""}
    try:
        session.get(f"http://127.0.0.1:{port_box[0]}/", timeout=5)
    except Exception:
        pass
    finally:
        session.close()
    thread.join(timeout=5)

    if not captured:
        return {}
    headers: dict[str, str] = {}
    for line in captured[0].split("\r\n")[1:]:
        if ":" in line:
            key, _, val = line.partition(":")
            headers[key.strip().lower()] = val.strip()
    return headers


def main() -> int:
    failures: list[str] = []

    if not PROFILES:
        print("[fail] không có profile nào")
        return 1

    names = [p.name for p in PROFILES]
    if len(names) != len(set(names)):
        failures.append(f"tên profile trùng: {names}")

    profile_fields = ("navigator_platform", "vendor", "has_user_agent_data", "hardware_options")
    missing_fields = [field for field in profile_fields if not hasattr(PROFILES[0], field)]
    if missing_fields:
        failures.append(f"Profile thiếu field navigator: {', '.join(missing_fields)}")
        for line in failures:
            print(f"[fail] {line}")
        print("[fail] fingerprint")
        return len(failures)

    # Constructor sáu tham số cũ vẫn phải hoạt động và dẫn xuất navigator fields.
    legacy_profile = Profile(
        "legacy",
        "chrome99",
        PROFILES[0].user_agent,
        "Windows",
        PROFILES[0].sec_ch_ua,
        PROFILES[0].accept_language,
    )
    if legacy_profile.navigator_platform != "Win32" or legacy_profile.vendor != "Google Inc.":
        failures.append("Profile constructor cũ không dẫn xuất navigator tương thích")

    engines = {re.match(r"[a-z]+", p.impersonate).group(0) for p in PROFILES}
    if len(engines) < 2:
        failures.append(f"chỉ 1 loại engine ({engines}) — thiếu đa dạng để xoay khi CF siết")

    try:
        get_profile("khong-ton-tai")
    except ValueError:
        pass
    else:
        failures.append("get_profile không fail-fast với tên lạ")

    # Bảng profile phải khớp header curl_cffi thật sự gửi.
    for profile in PROFILES:
        sent = _capture_headers(profile.impersonate)
        if not sent:
            failures.append(f"{profile.name}: không bắt được header")
            continue
        actual_ua = sent.get("user-agent", "")
        if actual_ua != profile.user_agent:
            failures.append(
                f"{profile.name}: UA lệch\n      bảng : {profile.user_agent}\n      thật : {actual_ua}"
            )
        actual_ch = sent.get("sec-ch-ua")
        if profile.sends_client_hints:
            if actual_ch != profile.sec_ch_ua:
                failures.append(
                    f"{profile.name}: sec-ch-ua lệch\n      bảng : {profile.sec_ch_ua}\n      thật : {actual_ch}"
                )
            platform = (sent.get("sec-ch-ua-platform") or "").strip('"')
            if platform != profile.platform:
                failures.append(f"{profile.name}: platform bảng={profile.platform} thật={platform}")
            expected_mobile = "?1" if profile.platform == "Android" else "?0"
            if sent.get("sec-ch-ua-mobile") != expected_mobile:
                failures.append(
                    f"{profile.name}: sec-ch-ua-mobile bảng={expected_mobile} "
                    f"thật={sent.get('sec-ch-ua-mobile')}"
                )
        elif actual_ch:
            failures.append(f"{profile.name}: khai không gửi client hints nhưng thật có gửi")

        if sent.get("accept-language") != profile.accept_language:
            failures.append(
                f"{profile.name}: Accept-Language lệch\n"
                f"      bảng : {profile.accept_language}\n"
                f"      thật : {sent.get('accept-language')}"
            )

        nav = navigator_payload(profile, _FIXED_SEED)
        if nav != navigator_payload(profile, _FIXED_SEED):
            failures.append(f"{profile.name}: cùng seed nhưng navigator không ổn định")
        if navigator_payload(profile) != navigator_payload(profile, DEFAULT_NAVIGATOR_SEED):
            failures.append(f"{profile.name}: seed=None không dùng fixed seed đã công bố")
        if nav["user_agent"] != profile.user_agent:
            failures.append(f"{profile.name}: navigator_payload UA lệch")
        if nav["platform"] != profile.navigator_platform:
            failures.append(f"{profile.name}: navigator platform lệch")
        if nav["vendor"] != profile.vendor:
            failures.append(f"{profile.name}: navigator vendor lệch")
        chromium_family = profile.name.startswith("edge") or profile.name.startswith("chrome")
        if profile.has_user_agent_data is not chromium_family:
            failures.append(f"{profile.name}: Profile khai sai userAgentData theo engine")
        if nav["has_user_agent_data"] is not chromium_family:
            failures.append(f"{profile.name}: navigator userAgentData family lệch")
        hardware = (nav["hardware_concurrency"], nav["device_memory"])
        if hardware not in profile.hardware_options:
            failures.append(f"{profile.name}: navigator hardware ngoài profile options: {hardware}")

        expected_brands = [
            {"brand": brand, "version": version}
            for brand, version in re.findall(r'"([^"]*)";v="([^"]*)"', profile.sec_ch_ua or "")
        ]
        if chromium_family:
            if nav["sec_ch_ua_brands"] != expected_brands:
                failures.append(f"{profile.name}: navigator CH brands lệch")
            if nav["sec_ch_ua_platform"] != profile.platform:
                failures.append(f"{profile.name}: navigator CH platform lệch")
            if nav["sec_ch_ua_mobile"] is not (profile.platform == "Android"):
                failures.append(f"{profile.name}: navigator CH mobile lệch")
        else:
            if nav["sec_ch_ua_brands"] or nav["sec_ch_ua_platform"] is not None:
                failures.append(f"{profile.name}: navigator không-CH lại có userAgentData")
            if nav["device_memory"] is not None:
                failures.append(f"{profile.name}: engine không-Chromium lại có deviceMemory")

    # Các hàm dựng header KHÔNG được đụng vào phần nhận dạng.
    from gpt_reg.phases import http_reg
    from gpt_reg.sentinel import pow as sentinel_pow
    from gpt_reg.sentinel import quickjs

    class _SentinelSession:
        gpt_profile = get_profile("safari153")
        gpt_fingerprint_seed = _FIXED_SEED

    sentinel_nav = quickjs._navigator_payload_for(_SentinelSession())
    wanted_nav = navigator_payload(get_profile("safari153"), _FIXED_SEED)
    if sentinel_nav != wanted_nav:
        failures.append("QuickJS navigator khong dung seed/profile cua session")
    try:
        quickjs._navigator_payload_for(object())
    except (RuntimeError, ValueError):
        pass
    else:
        failures.append("QuickJS thieu identity nhung fallback profile mac dinh")

    js_source = Path(quickjs.__file__).with_name("openai_sentinel_quickjs.js").read_text(
        encoding="utf-8"
    )
    for fragment in (
        'platform: "Win32"',
        'vendor: "Google Inc."',
        "deviceMemory: Number(",
        "userAgentData: {",
    ):
        if fragment in js_source:
            failures.append(f"QuickJS navigator hardcode Chromium: {fragment}")

    sdk_probe = """
var SentinelSDK={};
var _=class { async getRequirementsToken() {
  return JSON.stringify({
    platform:navigator.platform,
    vendor:navigator.vendor,
    hasUserAgentData:Object.prototype.hasOwnProperty.call(navigator,'userAgentData'),
    hasDeviceMemory:Object.prototype.hasOwnProperty.call(navigator,'deviceMemory'),
    hasChrome:typeof globalThis.chrome !== 'undefined'
  });
}};
var P=new _;
"""
    with tempfile.TemporaryDirectory() as tmp:
        sdk_file = Path(tmp) / "sdk.js"
        sdk_file.write_text(sdk_probe, encoding="utf-8")
        for profile_name in ("safari153", "chrome124"):
            profile = get_profile(profile_name)
            payload = navigator_payload(profile, _FIXED_SEED)
            result = quickjs._run_quickjs_action(
                action="requirements",
                sdk_file=sdk_file,
                quickjs_script=Path(quickjs.__file__).with_name(
                    "openai_sentinel_quickjs.js"
                ),
                payload={"device_id": "device", **payload},
                timeout_ms=10000,
            )
            runtime_nav = __import__("json").loads(result["request_p"])
            if runtime_nav["platform"] != profile.navigator_platform:
                failures.append(f"{profile_name}: QuickJS runtime platform lech")
            if runtime_nav["vendor"] != profile.vendor:
                failures.append(f"{profile_name}: QuickJS runtime vendor lech")
            expected_ch = profile.has_user_agent_data
            if runtime_nav["hasUserAgentData"] is not expected_ch:
                failures.append(f"{profile_name}: QuickJS runtime userAgentData sai")
            if runtime_nav["hasDeviceMemory"] is not expected_ch:
                failures.append(f"{profile_name}: QuickJS runtime deviceMemory sai")
            if runtime_nav["hasChrome"] is not expected_ch:
                failures.append(f"{profile_name}: QuickJS runtime chrome object sai")

    pow_hardware = {
        sentinel_pow._get_config("device", wanted_nav)[17]
        for _ in range(20)
    }
    if pow_hardware != {wanted_nav["hardware_concurrency"]}:
        failures.append(f"Python PoW random hardwareConcurrency: {pow_hardware}")

    captured_sentinel_headers: dict[str, str] = {}

    class _SentinelResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"token": "challenge"}

    class _SentinelCaptureSession(_SentinelSession):
        def post(self, url, *, data, headers, timeout):
            captured_sentinel_headers.update(headers)
            return _SentinelResponse()

    quickjs._fetch_sentinel_challenge(
        _SentinelCaptureSession(),
        device_id="device",
        flow="create_account",
        request_p="request",
        timeout_ms=10000,
    )
    sentinel_leaks = sorted(
        key for key in captured_sentinel_headers if key.lower() in _IDENTITY_HEADERS
    )
    if sentinel_leaks:
        failures.append(f"Sentinel tu ghi identity header: {sentinel_leaks}")

    class _FakeSession:
        gpt_profile = PROFILES[0]

    fake = _FakeSession()
    for label, headers in (
        ("_common_headers", http_reg._common_headers(fake, "https://chatgpt.com/")),
        ("_html_headers", http_reg._html_headers(fake, "https://chatgpt.com/")),
    ):
        for key in headers:
            if key.lower() in _IDENTITY_HEADERS:
                failures.append(f"{label} tự set header nhận dạng '{key}' — phá vân tay curl_cffi")
    if "traceparent" not in http_reg._common_headers(fake, "https://chatgpt.com/"):
        failures.append("_common_headers thiếu datadog trace (OTP có thể bị silent-drop)")

    for line in failures:
        print(f"[fail] {line}")
    if failures:
        print("[fail] fingerprint")
    else:
        print(f"[ok] fingerprint {len(PROFILES)} profile khớp curl_cffi, {len(engines)} engine")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
