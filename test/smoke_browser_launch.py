"""Smoke: proxy materialize."""
from gpt_reg.proxy.format import materialize_proxy


def main() -> None:
    mat = materialize_proxy("user:pass@host.example:8080")
    assert mat["server"] == "http://host.example:8080"
    assert mat["username"] == "user"
    print("[ok] proxy format")


if __name__ == "__main__":
    main()
