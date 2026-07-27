"""Kiểm tra client SMSBower bằng transport giả — không gọi mạng.

Phần quan trọng: API trả lỗi bằng **text thô 200 OK** (`NO_BALANCE`, `BAD_KEY`…)
chứ không dùng HTTP status, nên phải nhận diện đúng, nếu không sẽ coi chuỗi lỗi
là dữ liệu hợp lệ.
"""

from __future__ import annotations

import json

from gpt_reg.sms import SmsBowerClient, SmsBowerError


class _FakeResponse:
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status


class _FakeClient:
    """Thay httpx.Client — trả sẵn theo `action`."""

    routes: dict[str, str] = {}
    last_params: dict = {}

    def __init__(self, *_a, **_kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def get(self, _url, params=None):
        _FakeClient.last_params = dict(params or {})
        return _FakeResponse(_FakeClient.routes.get((params or {}).get("action"), ""))


def _client(monkey_routes: dict[str, str]) -> SmsBowerClient:
    import gpt_reg.sms.smsbower as mod

    _FakeClient.routes = monkey_routes
    mod.httpx.Client = _FakeClient  # type: ignore[assignment]
    return SmsBowerClient("test-key")


def main() -> int:
    import gpt_reg.sms.smsbower as mod

    real_client = mod.httpx.Client
    failures: list[str] = []
    try:
        # Số dư
        c = _client({"getBalance": "ACCESS_BALANCE:2.333"})
        if c.get_balance() != 2.333:
            failures.append("get_balance sai giá trị")

        # Lỗi text thô phải raise, không được coi là dữ liệu
        for marker in ("BAD_KEY", "NO_BALANCE", "BANNED:2026-01-01"):
            c = _client({"getBalance": marker})
            try:
                c.get_balance()
                failures.append(f"{marker} phải raise SmsBowerError")
            except SmsBowerError:
                pass

        # Tồn kho: bỏ nước hết hàng, sắp theo count giảm dần
        prices = {
            "12": {"go": {"cost": 0.171, "count": 577119}},
            "36": {"go": {"cost": 0.031, "count": 137908}},
            "99": {"go": {"cost": 0.5, "count": 0}},
            "98": {"other": {"cost": 0.1, "count": 50}},
        }
        c = _client({"getPrices": json.dumps(prices)})
        stocks = c.get_availability(countries={"12": "USA", "36": "Canada"})
        if [s.country_id for s in stocks] != ["12", "36"]:
            failures.append(f"get_availability sai thứ tự/lọc: {[s.country_id for s in stocks]}")
        if stocks and stocks[0].country_name != "USA":
            failures.append("không map được tên nước")

        # Thuê số
        c = _client({"getNumber": "ACCESS_NUMBER:12345:79991234567"})
        act = c.rent_number(country="36")
        if act.activation_id != "12345" or act.phone != "79991234567":
            failures.append(f"rent_number sai: {act}")
        if _FakeClient.last_params.get("service") != "go":
            failures.append("rent_number không gửi service=go")

        c = _client({"getNumber": "NO_NUMBERS"})
        try:
            c.rent_number(country="36")
            failures.append("NO_NUMBERS phải raise")
        except SmsBowerError:
            pass

        # Lấy mã
        c = _client({"getStatus": "STATUS_WAIT_CODE"})
        if c.get_code("1") is not None:
            failures.append("STATUS_WAIT phải trả None")
        c = _client({"getStatus": "STATUS_OK:483920"})
        if c.get_code("1") != "483920":
            failures.append("STATUS_OK không trả mã")

        # Thiếu key phải chặn ngay
        try:
            SmsBowerClient("")
            failures.append("key rỗng phải raise")
        except SmsBowerError:
            pass
    finally:
        mod.httpx.Client = real_client  # type: ignore[assignment]

    # Secret không được lộ qua /api/settings
    from gpt_reg.db.repositories import _SECRET_KEYS

    if "sms.smsbower.api_key" not in _SECRET_KEYS:
        failures.append("api_key SMSBower chưa nằm trong _SECRET_KEYS — sẽ lộ qua API")

    for line in failures:
        print(f"[fail] {line}")
    print("[fail] smsbower" if failures else "[ok] smsbower")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
