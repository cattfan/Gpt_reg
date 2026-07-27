"""Tab Check acc: parser combo, đọc plan, phân loại trạng thái, wiring manager.

Phần mạng (đăng nhập + gọi /accounts/check) chỉ verify được live — xem note.md.
Test này chốt phần thuần logic:
  - `CheckCombo.parse` với 3 field, 7 field (fullmail tự chứa |), 2fa rỗng, lỗi.
  - `_fetch_plan` bóc đúng plan_type/subscription từ JSON accounts/check thật.
  - `CheckError.kind` map sang status đúng (live/die/onboarding/error).
  - `clamp_check_concurrency` về nấc hợp lệ.
  - `check_manager.start_batch` tiền-kiểm combo, tạo đúng số hàng.
"""

from __future__ import annotations

from gpt_reg.checker.combo import CheckCombo, CheckComboError
from gpt_reg.checker.flow import CheckError, _fetch_plan, _PLAN_LABELS
from gpt_reg.web.jobs.check_manager import (
    _KIND_TO_STATUS, clamp_check_concurrency, CheckManager,
)

_UUID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"

# JSON thật rút gọn từ GET /backend-api/accounts/check (đo live 2026-07-27).
_CHECK_JSON_FREE = {
    "accounts": {
        "default": {
            "account": {"plan_type": "free", "is_deactivated": False},
            "entitlement": {"subscription_plan": "chatgptfreeplan", "has_active_subscription": False,
                            "expires_at": None},
        }
    }
}
_CHECK_JSON_PLUS = {
    "accounts": {
        "default": {
            "account": {"plan_type": "plus", "is_deactivated": False},
            "entitlement": {"subscription_plan": "chatgptplusplan", "has_active_subscription": True,
                            "expires_at": 1790000000},
        }
    }
}


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload

    def json(self):
        return self._p


class _Session:
    """Trả JSON đã set cho URL accounts/check; header không quan trọng ở đây."""

    gpt_profile = None

    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status

    def get(self, url, headers=None, timeout=None):
        return _Resp(self._status, self._payload)


def main() -> int:
    failures: list[str] = []

    # ── parser ──
    c = CheckCombo.parse("a@hotmail.com|Pw123456|JBSWY3DPEHPK3PXP")
    if not (c.email == "a@hotmail.com" and c.password == "Pw123456"
            and c.totp_secret == "JBSWY3DPEHPK3PXP" and not c.has_full_combo):
        failures.append(f"parse 3 field sai: {c}")

    full = f"a@hotmail.com|mailpw|refresh-tok|{_UUID}"
    c = CheckCombo.parse(f"a@hotmail.com|Pw123456|JBSW|{full}")
    if c.full_combo != full:
        failures.append(f"fullmail (7 field) không gộp đúng: {c.full_combo!r}")

    c = CheckCombo.parse("a@hotmail.com|Pw123456|")
    if c.has_totp:
        failures.append("2fa rỗng vẫn báo has_totp")

    for bad, why in [
        ("a@hotmail.com|Pw123456", "thiếu field 2fa"),
        ("notemail|pw|x", "email sai"),
        ("a@hotmail.com||JBSW", "thiếu pass"),
        ("a@b.com|pw|khong-base32!!", "2fa không base32"),
    ]:
        try:
            CheckCombo.parse(bad, line_number=3)
            failures.append(f"nhận nhầm combo sai ({why}): {bad!r}")
        except CheckComboError:
            pass

    # BOM ở đầu email phải bị bỏ.
    if CheckCombo.parse("﻿a@hotmail.com|pw|").email != "a@hotmail.com":
        failures.append("không strip BOM khỏi email")

    # ── plan parse ──
    plan = _fetch_plan(_Session(_CHECK_JSON_FREE), "tok", lambda _m: None)
    if plan["plan"] != "Free" or plan["has_subscription"]:
        failures.append(f"parse plan free sai: {plan}")
    plan = _fetch_plan(_Session(_CHECK_JSON_PLUS), "tok", lambda _m: None)
    if plan["plan"] != "Plus" or not plan["has_subscription"] or plan["plan_detail"] != "chatgptplusplan":
        failures.append(f"parse plan plus sai: {plan}")
    if plan["expires_at"] != 1790000000:
        failures.append("không lấy expires_at của gói trả phí")
    # plan lạ (server thêm mới) không được nuốt mất.
    weird = {"accounts": {"default": {"account": {"plan_type": "cosmic"}, "entitlement": {}}}}
    if _fetch_plan(_Session(weird), "tok", lambda _m: None)["plan"] != "cosmic":
        failures.append("plan lạ bị nuốt thay vì hiển thị thô")
    # accounts/check non-200 → CheckError kind=error.
    try:
        _fetch_plan(_Session({}, status=403), "tok", lambda _m: None)
        failures.append("accounts/check 403 không ném lỗi")
    except CheckError as exc:
        if exc.kind != "error":
            failures.append(f"403 phân loại sai kind: {exc.kind}")

    # ── status mapping ──
    if _KIND_TO_STATUS["die"] != "die" or _KIND_TO_STATUS["not_found"] != "die":
        failures.append("die/not_found không map sang 'die'")
    if _KIND_TO_STATUS["onboarding"] != "onboarding":
        failures.append("onboarding map sai")
    if _KIND_TO_STATUS["need_2fa"] != "error" or _KIND_TO_STATUS["need_fullmail"] != "error":
        failures.append("need_2fa/need_fullmail phải là 'error' (thêm info rồi retry được)")

    # ── concurrency clamp ──
    if clamp_check_concurrency(50) != 50 or clamp_check_concurrency(7) != 5:
        failures.append("clamp_check_concurrency sai nấc")
    if clamp_check_concurrency(9999) != 200 or clamp_check_concurrency("x") != 1:
        failures.append("clamp_check_concurrency không chặn trần / input rác")

    # ── manager pre-validate + tạo hàng ──
    created = []

    class _Repo:
        def create(self, row): created.append(row)
        def update(self, *a, **k): pass
        def get(self, i): return None

    mgr = CheckManager()
    ids = mgr.start_batch(
        combos=["a@hotmail.com|pw1|JBSW", "  ", "b@hotmail.com|pw2|"],
        checks_repo=_Repo(), proxy_pool_text="", concurrency=1,
    )
    if len(ids) != 2 or len(created) != 2:
        failures.append(f"start_batch tạo sai số hàng (bỏ dòng trống?): ids={len(ids)} rows={len(created)}")
    # combo hỏng ở giữa → ném CheckComboError, KHÔNG tạo hàng nào (all-or-nothing).
    created.clear()
    mgr2 = CheckManager()
    try:
        mgr2.start_batch(combos=["a@hotmail.com|pw|JBSW", "dong-hong"],
                         checks_repo=_Repo(), proxy_pool_text="", concurrency=1)
        failures.append("combo hỏng không bị chặn trước khi tạo hàng")
    except CheckComboError:
        if created:
            failures.append("đã tạo hàng trước khi phát hiện combo hỏng (kẹt queued)")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] checker" if failures else f"[ok] checker (parser+plan+status, plans={len(_PLAN_LABELS)})")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
