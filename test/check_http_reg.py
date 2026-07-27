"""Kiểm tra phần logic thuần (không mạng) của HTTP reg phase.

Phần mạng (prime CF, sentinel, register) chỉ verify được qua live run — xem
note.md. Test này chốt: thứ tự fingerprint ưu tiên loại CF-friendly, nhận diện
CF block để rotate, cookie helper, và phase đăng ký đúng interface.
"""

from __future__ import annotations

import inspect

from gpt_reg.core.exceptions import HttpRegError
from gpt_reg.phases import http_reg as hr


_SEED = "42" * 16
_IDENTITY_HEADERS = {
    "user-agent",
    "accept-language",
    "accept-encoding",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
}


class _FakeCookie:
    def __init__(self, name, value, domain):
        self.name, self.value, self.domain = name, value, domain


class _FakeJar:
    def __init__(self, cookies):
        self._c = cookies

    def __iter__(self):
        return iter(self._c)


class _FakeCookies:
    def __init__(self, cookies):
        self.jar = _FakeJar(cookies)

    def get(self, name, domain=None):
        raise Exception("force jar scan")


class _FakeSession:
    def __init__(self, cookies):
        self.cookies = _FakeCookies(cookies)


class _Response:
    status_code = 200

    @staticmethod
    def json():
        return {"accessToken": "token", "user": {"id": "user", "email": "a@x.test"}}


def _check_rotation_identity(failures: list[str]) -> None:
    from gpt_reg.fingerprint import PROFILES, candidate_profiles, device_id_for_seed

    preferred = PROFILES[3].name
    wanted = candidate_profiles(_SEED, preferred)
    created = []
    auth_device_ids: list[str] = []

    class _RotationSession:
        def __init__(self, profile, seed):
            self.gpt_profile = profile
            self.gpt_fingerprint_seed = seed
            self.closed = False

        def close(self):
            self.closed = True

    original = (
        hr._create_session,
        hr._step_csrf,
        hr._step_auth_url,
        hr._step_oauth_init,
    )
    csrf_calls = {"count": 0}

    def fake_create(proxy, profile, *, fingerprint_seed):
        session = _RotationSession(profile, fingerprint_seed)
        created.append(session)
        return session

    def fake_csrf(session, log):
        csrf_calls["count"] += 1
        if csrf_calls["count"] == 1:
            raise HttpRegError("blocked", step="cf_block")
        return "csrf"

    def fake_auth(session, csrf_token, log, *, device_id="", login_hint="", **_kw):
        auth_device_ids.append(device_id)
        return "https://auth.openai.com/authorize"

    def fake_oauth(session, auth_url, log):
        return "", "https://auth.openai.com/create-account/password"

    try:
        hr._create_session = fake_create
        hr._step_csrf = fake_csrf
        hr._step_auth_url = fake_auth
        hr._step_oauth_init = fake_oauth
        try:
            session, device_id, _landing, _auth_url = hr._bootstrap_with_profile_rotation(
                None,
                lambda _line: None,
                fingerprint_seed=_SEED,
                preferred_profile=preferred,
                login_hint="a@x.test",
            )
        except TypeError as exc:
            failures.append(f"bootstrap chua nhan identity: {exc}")
            return

        if [s.gpt_profile for s in created] != [wanted[0], wanted[1]]:
            failures.append("bootstrap khong dung candidate order theo seed")
        if not created[0].closed or created[1].closed:
            failures.append("bootstrap khong dong dung session thua")
        expected_device = device_id_for_seed(_SEED, "http")
        if device_id != expected_device or auth_device_ids != [expected_device]:
            failures.append("bootstrap khong giu device ID theo seed")
        if session.gpt_profile is not wanted[1]:
            failures.append("bootstrap khong tra profile thang rotation")
        session.close()

        # Once authorize has returned an auth URL, a later CF/TLS error must not
        # silently switch persona inside the same auth state.
        created.clear()
        csrf_calls["count"] = 99

        def fail_after_auth(session, auth_url, log):
            raise HttpRegError("blocked after auth", step="cf_block")

        hr._step_oauth_init = fail_after_auth
        try:
            hr._bootstrap_with_profile_rotation(
                None,
                lambda _line: None,
                fingerprint_seed=_SEED,
                preferred_profile=preferred,
                login_hint="a@x.test",
            )
        except HttpRegError:
            pass
        else:
            failures.append("bootstrap nuot loi sau khi auth state da tao")
        if len(created) != 1:
            failures.append("bootstrap doi profile sau khi auth state da tao")
    finally:
        (
            hr._create_session,
            hr._step_csrf,
            hr._step_auth_url,
            hr._step_oauth_init,
        ) = original


def _check_phase2_headers(failures: list[str]) -> None:
    from gpt_reg.models import SignupRequest
    from gpt_reg.phases import http as phase2

    captured: dict[str, str] = {}

    class _CaptureSession:
        def get(self, url, *, headers, timeout):
            captured.update(headers)
            return _Response()

    kwargs = {"session": _CaptureSession(), "log": lambda _line: None}
    if "request" in inspect.signature(phase2._fetch_access_token).parameters:
        kwargs["request"] = SignupRequest(email="a@x.test", fingerprint_seed=_SEED)
    phase2._fetch_access_token(**kwargs)
    leaked = sorted(_IDENTITY_HEADERS.intersection(key.lower() for key in captured))
    if leaked:
        failures.append(f"phase2 tu ghi de identity header: {leaked}")
    if captured.get("Accept") != "application/json" or "Referer" not in captured:
        failures.append(f"phase2 thieu header ngu canh: {captured}")


def _check_sentinel_fail_fast(failures: list[str]) -> None:
    from gpt_reg.fingerprint import get_profile
    from gpt_reg.sentinel import pow as sentinel_pow
    from gpt_reg.sentinel import quickjs

    class _IdentitySession:
        gpt_profile = get_profile("safari153")
        gpt_fingerprint_seed = _SEED

    quickjs_calls = {"count": 0}
    pow_calls = {"count": 0}
    original_quickjs = quickjs.get_sentinel_token_via_quickjs
    original_pow = sentinel_pow.get_sentinel_token
    original_sleep = hr.time.sleep

    def fail_quickjs(*_args, **_kwargs):
        quickjs_calls["count"] += 1
        raise RuntimeError("quickjs failed")

    def fake_pow(*_args, **_kwargs):
        pow_calls["count"] += 1
        return "pow-token"

    quickjs.get_sentinel_token_via_quickjs = fail_quickjs
    sentinel_pow.get_sentinel_token = fake_pow
    hr.time.sleep = lambda _seconds: None
    try:
        try:
            hr._get_sentinel_token(
                _IdentitySession(),
                "device",
                "create_account",
                lambda _line: None,
            )
        except RuntimeError:
            pass
        else:
            failures.append("QuickJS loi nhung HTTP am tham doi sang Python PoW")
        if quickjs_calls["count"] != hr._SENTINEL_QUICKJS_ATTEMPTS:
            failures.append("QuickJS khong retry dung so lan")
        if pow_calls["count"]:
            failures.append("QuickJS loi da goi Python PoW ngam")
    finally:
        quickjs.get_sentinel_token_via_quickjs = original_quickjs
        sentinel_pow.get_sentinel_token = original_pow
        hr.time.sleep = original_sleep


def main() -> int:
    failures: list[str] = []

    # Vân tay: profile đo được là qua Cloudflare phải đứng TRƯỚC những cái đang
    # bị chặn (chi tiết ở check_fingerprint.py + test/probe_fingerprints.py).
    from gpt_reg.fingerprint import PROFILES, device_id_for_seed

    names = [p.name for p in PROFILES]
    if "chrome145" in names and names.index("chrome145") < len(names) - 2:
        failures.append("chrome145 đang bị CF chặn — không được xếp lên đầu")
    if not any(p.impersonate.startswith("safari") for p in PROFILES):
        failures.append("thiếu profile Safari — mất đường xoay khi CF siết Chrome")
    # `_create_session` phải đính profile để header và TLS không lệch nhau.
    try:
        session = hr._create_session(None, PROFILES[1], fingerprint_seed=_SEED)
    except TypeError as exc:
        failures.append(f"_create_session chua nhan seed: {exc}")
    else:
        try:
            if hr._profile_of(session) is not PROFILES[1]:
                failures.append("_create_session không đính profile vào session")
            if getattr(session, "gpt_fingerprint_seed", None) != _SEED:
                failures.append("_create_session khong dinh seed vao session")
            expected_did = device_id_for_seed(_SEED, "http")
            did_cookies = {
                cookie.domain: cookie.value
                for cookie in session.cookies.jar
                if cookie.name == "oai-did"
            }
            if did_cookies != {
                ".chatgpt.com": expected_did,
                ".openai.com": expected_did,
            }:
                failures.append("_create_session chua dong bo oai-did voi fingerprint identity")
        finally:
            session.close()

    # CF block phải rotate; lỗi thường thì không.
    if not hr._is_cf_block(HttpRegError("x", step="cf_block")):
        failures.append("_is_cf_block miss cf_block")
    if hr._is_cf_block(HttpRegError("x", step="register")):
        failures.append("_is_cf_block dương tính giả")
    if hr._is_cf_block(ValueError("403")):
        failures.append("_is_cf_block bắt nhầm lỗi thường")
    try:
        hr._profile_of(object())
    except (RuntimeError, ValueError):
        pass
    else:
        failures.append("_profile_of thieu identity nhung fallback profile mac dinh")

    # Cookie helper đọc được qua jar khi .get() ném CookieConflict.
    sess = _FakeSession([
        _FakeCookie("__Secure-next-auth.session-token", "TOK", ".chatgpt.com"),
        _FakeCookie("oai-did", "DID", ".openai.com"),
    ])
    if hr._cookie_get(sess, "__Secure-next-auth.session-token", domain_preference=(".chatgpt.com",)) != "TOK":
        failures.append("_cookie_get không đọc được session-token")
    if not hr._cookie_has(sess, "oai-did", domain_preference=(".openai.com",)):
        failures.append("_cookie_has miss oai-did")
    if hr._cookie_has(sess, "khong-ton-tai"):
        failures.append("_cookie_has dương tính giả")

    # _domain_matches
    if not hr._domain_matches(".chatgpt.com", "chatgpt.com"):
        failures.append("_domain_matches miss subdomain")
    if hr._domain_matches(".openai.com", "chatgpt.com"):
        failures.append("_domain_matches dương tính giả")

    _check_rotation_identity(failures)
    _check_phase2_headers(failures)
    _check_sentinel_fail_fast(failures)

    # Header builders: có datadog trace (thiếu là OTP silent-drop) nhưng KHÔNG
    # được tự set phần nhận dạng — curl_cffi lo (xem check_fingerprint.py).
    h = hr._common_headers(sess, "https://auth.openai.com/create-account/password")
    for key in ("traceparent", "x-datadog-trace-id", "Origin", "Referer"):
        if key not in h:
            failures.append(f"_common_headers thiếu {key}")
    if h.get("Origin") != "https://auth.openai.com":
        failures.append(f"Origin sai: {h.get('Origin')}")

    # Phase interface khớp BrowserPhase + đăng ký đúng key.
    from gpt_reg.phases.browser import BrowserPhase
    from gpt_reg.phases.registry import available_modes, get_phase

    if "http" not in available_modes():
        failures.append("http chưa đăng ký trong registry")
    phase = get_phase("http")
    if phase.mode != "http":
        failures.append(f"mode sai: {phase.mode}")
    if set(inspect.signature(BrowserPhase.run).parameters) != set(inspect.signature(hr.HttpRegPhase.run).parameters):
        failures.append("HttpRegPhase.run interface lệch BrowserPhase")
    if not inspect.iscoroutinefunction(phase.run):
        failures.append("run không phải coroutine")

    from gpt_reg.models import BrowserHandoff

    for field in ("user_agent", "impersonate", "fingerprint_profile"):
        if field not in BrowserHandoff.model_fields:
            failures.append(f"BrowserHandoff thieu {field}")

    from gpt_reg.phases.mfa import enable_2fa

    mfa_params = inspect.signature(enable_2fa).parameters
    if "fingerprint_profile" not in mfa_params:
        failures.append("enable_2fa chua nhan fingerprint_profile")
    if "user_agent" in mfa_params or "impersonate" in mfa_params:
        failures.append("enable_2fa van nhan UA/impersonate roi")
    mfa_source = inspect.getsource(enable_2fa).lower()
    mfa_identity_headers = sorted(
        header for header in _IDENTITY_HEADERS if f'"{header}"' in mfa_source
    )
    if mfa_identity_headers:
        failures.append(f"enable_2fa tu ghi identity header: {mfa_identity_headers}")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] http reg" if failures else "[ok] http reg")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
