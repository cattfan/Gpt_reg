"""Smoke: import package."""
import gpt_reg
from gpt_reg.phases.registry import get_phase


def main() -> None:
    assert gpt_reg.run_signup is not None
    assert get_phase("browser").mode == "browser"
    print("[ok] imports")


if __name__ == "__main__":
    main()
