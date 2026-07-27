"""Check mail mode registry."""
from gpt_reg.mail.modes import get_registry, get_spec


def main() -> None:
    reg = get_registry()
    assert "outlook" in reg
    assert get_spec("outlook").id == "outlook"
    print("[ok] mail registry")


if __name__ == "__main__":
    main()
