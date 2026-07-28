"""Gmail registration must not let login_hint hand OAuth to Google SSO."""

from __future__ import annotations

from types import SimpleNamespace

from gpt_reg.models import SignupRequest
from gpt_reg.phases import http_reg as hr


class _StopBootstrap(Exception):
    pass


def _capture_bootstrap(request: SignupRequest) -> dict[str, object]:
    captured: dict[str, object] = {}
    original = hr._bootstrap_with_profile_rotation

    def fake_bootstrap(proxy, log, **kwargs):
        del proxy, log
        captured.update(kwargs)
        raise _StopBootstrap

    hr._bootstrap_with_profile_rotation = fake_bootstrap
    try:
        try:
            hr._run_flow(
                SimpleNamespace(should_cancel=None),
                request,
                object(),
                None,
                lambda _line: None,
                [],
            )
        except _StopBootstrap:
            pass
    finally:
        hr._bootstrap_with_profile_rotation = original
    return captured


def _check_external_landing_stops(failures: list[str]) -> None:
    original = hr._bootstrap_with_profile_rotation

    class _ExternalSession:
        def get(self, *_args, **_kwargs):
            raise AssertionError("flow navigated after external OAuth landing")

        def post(self, *_args, **_kwargs):
            raise AssertionError("flow posted user/register after external OAuth landing")

    def fake_bootstrap(*_args, **_kwargs):
        return (
            _ExternalSession(),
            "device-id",
            "https://accounts.google.com/v3/signin/identifier",
            "https://auth.openai.com/authorize",
        )

    hr._bootstrap_with_profile_rotation = fake_bootstrap
    try:
        try:
            hr._run_flow(
                SimpleNamespace(should_cancel=None),
                SignupRequest(
                    email="fixture@gmail.com",
                    mail_provider="gmail_smsbower",
                    reg_mode="http",
                ),
                object(),
                None,
                lambda _line: None,
                [],
            )
        except hr.HttpRegError as exc:
            if exc.step != "external_identity":
                failures.append(f"external landing used wrong error step: {exc.step!r}")
        except AssertionError as exc:
            failures.append(str(exc))
        else:
            failures.append("external OAuth landing did not stop the HTTP flow")
    finally:
        hr._bootstrap_with_profile_rotation = original


def _check_base_gmail_external_continue_stops(failures: list[str]) -> None:
    original_bootstrap = hr._bootstrap_with_profile_rotation
    original_continue = hr._step_authorize_continue

    class _Response:
        status_code = 200
        text = '{}'

        def json(self):
            raise AssertionError("flow posted user/register after external authorize/continue")

    class _Session:
        def get(self, *_args, **_kwargs):
            return _Response()

        def post(self, url, *_args, **_kwargs):
            if str(url).endswith('/api/accounts/user/register'):
                raise AssertionError("flow posted user/register after external authorize/continue")
            return _Response()

    def fake_bootstrap(*_args, **_kwargs):
        return (
            _Session(),
            "device-id",
            "https://auth.openai.com/create-account",
            "https://auth.openai.com/authorize",
        )

    def fake_continue(*_args, **_kwargs):
        return {"page": {"type": "external_url"}, "continue_url": "https://accounts.google.com/o/oauth2/v2/auth"}

    hr._bootstrap_with_profile_rotation = fake_bootstrap
    hr._step_authorize_continue = fake_continue
    try:
        try:
            hr._run_flow(
                SimpleNamespace(should_cancel=None),
                SignupRequest(
                    email="fixture@gmail.com",
                    mail_provider="gmail_smsbower",
                    reg_mode="http",
                ),
                object(),
                None,
                lambda _line: None,
                [],
            )
        except hr.HttpRegError as exc:
            if exc.step != "external_identity":
                failures.append(f"external continue used wrong error step: {exc.step!r}")
        except AssertionError as exc:
            failures.append(str(exc))
        else:
            failures.append("external authorize/continue did not stop the HTTP flow")
    finally:
        hr._bootstrap_with_profile_rotation = original_bootstrap
        hr._step_authorize_continue = original_continue


def main() -> int:
    failures: list[str] = []

    for provider in ("gmail_smsbower", "gmail_accstack"):
        request = SignupRequest(
            email="fixture@gmail.com",
            mail_provider=provider,
            reg_mode="http",
        )
        captured = _capture_bootstrap(request)
        if captured.get("login_hint") != "" or captured.get("screen_hint") != "signup":
            failures.append(f"new {provider} bootstrap still exposes login_hint: {captured!r}")

    resumed = _capture_bootstrap(
        SignupRequest(
            email="fixture@gmail.com",
            password="saved-account-password",
            mail_provider="gmail_smsbower",
            reg_mode="http",
        )
    )
    if resumed.get("login_hint") != "fixture@gmail.com":
        failures.append(f"Gmail resume lost account discovery: {resumed!r}")

    if hr.classify_landing("https://accounts.google.com/v3/signin/identifier") != "external":
        failures.append("Google OAuth landing is not classified as external")
    _check_external_landing_stops(failures)
    _check_base_gmail_external_continue_stops(failures)

    for failure in failures:
        print(f"[fail] {failure}")
    print("[fail] http Gmail bootstrap" if failures else "[ok] http Gmail bootstrap")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)

