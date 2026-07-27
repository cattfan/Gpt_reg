"""Kiểm tra `about_you.classify_field` — nhận diện field theo metadata DOM.

OpenAI A/B test nhiều biến thể form: `age`, `birth year`, một ô birthday đầy đủ,
hoặc 3 ô month/day/year tách rời. Điền theo tab order sẽ gõ nhầm vào ô name.
"""

from __future__ import annotations

from gpt_reg.phases.browser import about_you

CASES = (
    ({"name": "age", "type": "number"}, "age"),
    ({"name": "birthyear"}, "year"),
    ({"name": "birthdate"}, "birthday"),
    ({"autocomplete": "bday-year"}, "year"),
    ({"autocomplete": "bday-month"}, "month"),
    ({"autocomplete": "bday-day"}, "day"),
    ({"autocomplete": "bday"}, "birthday"),
    ({"type": "date"}, "birthday"),
    ({"type": "text", "label": "Full name", "name": "name"}, None),
    ({"type": "hidden", "name": "csrf"}, None),
    ({"type": "number", "min": "1900", "max": "2012"}, "year"),
    ({"type": "number", "min": "13", "max": "120"}, "age"),
    ({"placeholder": "MM"}, "month"),
    ({"placeholder": "DD"}, "day"),
    ({"placeholder": "YYYY"}, "year"),
    ({"aria_label": "Date of birth"}, "birthday"),
)


def main() -> int:
    failed = 0
    for meta, expected in CASES:
        got = about_you.classify_field(meta)
        if got != expected:
            failed += 1
            print(f"[fail] {meta} -> {got} (want {expected})")

    values = about_you.birth_values("2000-03-07")
    if (values["year"], values["month"], values["day"]) != ("2000", "3", "7"):
        failed += 1
        print(f"[fail] birth_values parts: {values}")
    if not 18 <= int(values["age"]) <= 80:
        failed += 1
        print(f"[fail] birth_values age out of range: {values['age']}")

    if failed:
        print(f"[fail] about-you fields ({failed} lỗi)")
    else:
        print(f"[ok] about-you fields {len(CASES)}/{len(CASES)}")
    return failed


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
