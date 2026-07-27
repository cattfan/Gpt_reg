"""HTTP handoff must carry one canonical profile into downstream phases."""

from __future__ import annotations


def _raises(call, error_type) -> bool:
    try:
        call()
    except error_type:
        return True
    return False


def main() -> int:
    from gpt_reg.fingerprint import get_profile
    from gpt_reg.models import BrowserHandoff, SignupRequest
    from gpt_reg.phases.http import HttpPhaseError
    from gpt_reg.signup import _network_request_for_handoff

    failures: list[str] = []
    seed = "61" * 16
    primary = get_profile("chrome124")
    rotated = get_profile("safari184")
    request = SignupRequest(
        email="a@x.test",
        fingerprint_seed=seed,
        fingerprint_profile=primary.name,
        user_agent=primary.user_agent,
        impersonate=primary.impersonate,
    )

    browser_request = _network_request_for_handoff(request, BrowserHandoff())
    if browser_request != request:
        failures.append("browser handoff rong da doi HTTP profile")

    handoff = BrowserHandoff(
        user_agent=rotated.user_agent,
        impersonate=rotated.impersonate,
        fingerprint_profile=rotated.name,
    )
    network_request = _network_request_for_handoff(request, handoff)
    if network_request.fingerprint_profile != rotated.name:
        failures.append("HTTP handoff khong truyen profile thang rotation")
    if network_request.user_agent != rotated.user_agent:
        failures.append("HTTP handoff khong truyen UA thang rotation")
    if network_request.impersonate != rotated.impersonate:
        failures.append("HTTP handoff khong truyen impersonate thang rotation")
    if network_request.fingerprint_seed != seed:
        failures.append("HTTP handoff da doi fingerprint seed")

    partial = BrowserHandoff(fingerprint_profile=rotated.name)
    if not _raises(
        lambda: _network_request_for_handoff(request, partial),
        HttpPhaseError,
    ):
        failures.append("handoff identity thieu field khong fail-fast")

    mismatch = handoff.model_copy(update={"user_agent": primary.user_agent})
    if not _raises(
        lambda: _network_request_for_handoff(request, mismatch),
        HttpPhaseError,
    ):
        failures.append("handoff identity mismatch khong fail-fast")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] signup fingerprint flow" if failures else "[ok] signup fingerprint flow")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
