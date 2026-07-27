"""Probe live: NextAuth `screen_hint=login` + login_hint + OTP bằng HTTP thuần.

Chạy: python test/probe_http_otp_login.py "<combo>"
"""

from __future__ import annotations

import json
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gpt_reg.config import ensure_runtime_dirs, load_settings
from gpt_reg.db import connect, migrate
from gpt_reg.db.repositories import SettingsRepository
from gpt_reg.fingerprint import device_id_for_seed, profile_for_seed
from gpt_reg.mail.modes import parse_outlook_combo
from gpt_reg.mail.outlook import OutlookMailProvider
from gpt_reg.phases import http_reg as hr
from gpt_reg.proxy.pool import ProxyPool


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    combo = parse_outlook_combo(sys.argv[1].strip())
    settings = load_settings()
    ensure_runtime_dirs(settings)
    conn = connect(settings.runtime_dir / "data.db")
    migrate(conn)
    proxy = ProxyPool.from_multiline(
        SettingsRepository(conn).get("proxy.pool") or ""
    ).acquire_url()
    mail = OutlookMailProvider(
        combo=combo, state_dir=settings.outlook_state_dir, proxy_url=proxy
    )

    def log(line: str) -> None:
        print(line)

    fingerprint_seed = hashlib.sha256(
        f"probe-http-otp:{combo.email.strip().lower()}".encode("utf-8")
    ).hexdigest()[:32]
    profile = profile_for_seed(fingerprint_seed)
    session = hr._create_session(proxy, profile, fingerprint_seed=fingerprint_seed)
    try:
        device_id = device_id_for_seed(fingerprint_seed, "http")
        csrf = hr._step_csrf(session, log)
        auth_url = hr._step_auth_url(
            session, csrf, log, device_id=device_id, login_hint=combo.email,
            screen_hint="login",
        )
        device_id, landing = hr._step_oauth_init(session, auth_url, log)
        print(f"landing={hr.classify_landing(landing)} path={landing.split('?')[0]}")
        since = datetime.now(timezone.utc)
        hr._step_send_otp(session, device_id, log)
        request = type(
            "OtpRequest", (), {
                "email": combo.email,
                "otp_timeout_seconds": 180.0,
                "otp_poll_interval_seconds": 3.0,
            }
        )()
        code, _ = hr._poll_otp(mail, request, since, set(), None, log)
        response = hr._step_verify_otp(session, code, device_id, log)
        public = {key: value for key, value in response.items() if not key.startswith("_")}
        print(json.dumps(public, ensure_ascii=False)[:1000])
        continue_url = str(response.get("continue_url") or auth_url)
        callback = hr._step_follow_redirects(session, continue_url, log)
        if callback:
            hr._consume_callback(session, callback, log)
        session_token, access_token, _, email = hr._get_session_tokens(session, log)
        print(
            f"callback={bool(callback)} session={bool(session_token)} "
            f"access={bool(access_token)} email_match={email.lower() == combo.email.lower()}"
        )
        return 0 if session_token or access_token else 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
