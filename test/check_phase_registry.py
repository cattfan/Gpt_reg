"""Check phase registry."""
from gpt_reg.phases.registry import get_phase


def main() -> None:
    phase = get_phase("browser")
    assert phase.mode == "browser"
    print("[ok] phase registry")


if __name__ == "__main__":
    main()
