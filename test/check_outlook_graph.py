"""Optional Graph check — set OUTLOOK_COMBO env to run live test."""
import os
import sys


def main() -> None:
    combo = os.environ.get("OUTLOOK_COMBO", "").strip()
    if not combo:
        print("[skip] OUTLOOK_COMBO not set")
        return
    from gpt_reg.config import load_settings, ensure_runtime_dirs
    from gpt_reg.mail.modes import parse_outlook_combo
    from gpt_reg.mail.outlook import OutlookMailProvider

    settings = load_settings()
    ensure_runtime_dirs(settings)
    provider = OutlookMailProvider(
        combo=parse_outlook_combo(combo),
        state_dir=settings.outlook_state_dir,
    )
    subjects = provider.list_subjects(limit=3)
    print("[ok] graph", subjects)


if __name__ == "__main__":
    main()
