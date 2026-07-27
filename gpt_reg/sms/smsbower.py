"""Client SMSBower — thuê số điện thoại nhận SMS (dùng cho nguồn Gmail).

Giao thức SMS-activate chuẩn: mọi thứ đi qua một endpoint với `action=`, trả về
text thô dạng `TIỀN_TỐ:giá_trị` hoặc JSON tuỳ action.

Số dư và tồn kho là hai thứ UI cần hiển thị:
  - `get_balance()`      → số dư tài khoản
  - `get_availability()` → giá + số lượng số còn cho từng quốc gia

API key lưu trong SQLite (`sms.smsbower.api_key`), không hardcode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://smsbower.online/stubs/handler_api.php"

# Mã dịch vụ theo chuẩn SMS-activate.
SERVICE_GOOGLE = "go"

# Lỗi API trả bằng text thô — coi là lỗi chứ không phải dữ liệu.
_ERROR_MARKERS = frozenset({
    "BAD_KEY", "BAD_ACTION", "BAD_SERVICE", "BAD_STATUS", "ERROR_SQL",
    "NO_BALANCE", "NO_NUMBERS", "NO_ACTIVATION", "WRONG_SERVICE",
    "WRONG_ACTIVATION_ID", "BANNED", "ACCOUNT_INACTIVE", "NO_KEY",
})


class SmsBowerError(Exception):
    """Gọi SMSBower thất bại."""


@dataclass(frozen=True)
class CountryStock:
    country_id: str
    country_name: str
    cost: float
    count: int


@dataclass(frozen=True)
class Activation:
    activation_id: str
    phone: str


class SmsBowerClient:
    def __init__(self, api_key: str, *, proxy_url: str | None = None, timeout: float = 25.0):
        if not api_key or not api_key.strip():
            raise SmsBowerError("thiếu API key SMSBower")
        self._key = api_key.strip()
        self._proxy = proxy_url
        self._timeout = timeout

    # ── transport ────────────────────────────────────────────────────────

    def _call(self, action: str, **params: Any) -> str:
        query = {"api_key": self._key, "action": action}
        query.update({k: str(v) for k, v in params.items() if v is not None})
        try:
            with httpx.Client(proxy=self._proxy, timeout=self._timeout) as client:
                response = client.get(BASE_URL, params=query)
        except Exception as exc:
            raise SmsBowerError(f"{action}: lỗi mạng {type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise SmsBowerError(f"{action}: HTTP {response.status_code}")
        text = (response.text or "").strip()
        head = text.split(":", 1)[0].strip()
        if head in _ERROR_MARKERS:
            raise SmsBowerError(f"{action}: {text[:120]}")
        return text

    def _call_json(self, action: str, **params: Any) -> Any:
        import json

        text = self._call(action, **params)
        try:
            return json.loads(text)
        except Exception as exc:
            raise SmsBowerError(f"{action}: response không phải JSON: {text[:120]}") from exc

    # ── số dư & tồn kho ──────────────────────────────────────────────────

    def get_balance(self) -> float:
        """Số dư tài khoản. Response dạng `ACCESS_BALANCE:2.333`."""
        text = self._call("getBalance")
        _, _, value = text.partition(":")
        try:
            return float(value.strip())
        except ValueError as exc:
            raise SmsBowerError(f"getBalance: không đọc được số dư từ {text[:60]!r}") from exc

    def get_countries(self) -> dict[str, str]:
        """`{country_id: tên tiếng Anh}`."""
        data = self._call_json("getCountries")
        if not isinstance(data, dict):
            raise SmsBowerError("getCountries: kiểu dữ liệu lạ")
        return {
            str(cid): str((info or {}).get("eng") or cid)
            for cid, info in data.items()
        }

    def get_availability(
        self,
        service: str = SERVICE_GOOGLE,
        *,
        countries: dict[str, str] | None = None,
        limit: int = 30,
    ) -> list[CountryStock]:
        """Giá + số lượng số còn, sắp theo tồn kho giảm dần."""
        data = self._call_json("getPrices", service=service)
        if not isinstance(data, dict):
            raise SmsBowerError("getPrices: kiểu dữ liệu lạ")
        names = countries if countries is not None else {}
        stocks: list[CountryStock] = []
        for country_id, services in data.items():
            entry = (services or {}).get(service) if isinstance(services, dict) else None
            if not isinstance(entry, dict):
                continue
            try:
                count = int(entry.get("count") or 0)
                cost = float(entry.get("cost") or 0)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            stocks.append(
                CountryStock(
                    country_id=str(country_id),
                    country_name=names.get(str(country_id), str(country_id)),
                    cost=cost,
                    count=count,
                )
            )
        stocks.sort(key=lambda s: s.count, reverse=True)
        return stocks[:limit] if limit else stocks

    # ── vòng đời thuê số ─────────────────────────────────────────────────

    def rent_number(self, *, service: str = SERVICE_GOOGLE, country: str) -> Activation:
        """Thuê 1 số. Response `ACCESS_NUMBER:<id>:<phone>`."""
        text = self._call("getNumber", service=service, country=country)
        parts = text.split(":")
        if len(parts) < 3 or parts[0] != "ACCESS_NUMBER":
            raise SmsBowerError(f"getNumber: response lạ {text[:80]!r}")
        return Activation(activation_id=parts[1].strip(), phone=parts[2].strip())

    def set_status(self, activation_id: str, status: int) -> str:
        """status: 1=sẵn sàng nhận, 3=xin mã lại, 6=hoàn tất, 8=huỷ (hoàn tiền)."""
        return self._call("setStatus", id=activation_id, status=status)

    def get_code(self, activation_id: str) -> str | None:
        """Lấy mã SMS. None = chưa tới. Response `STATUS_OK:<code>`."""
        text = self._call("getStatus", id=activation_id)
        if text.startswith("STATUS_OK"):
            _, _, code = text.partition(":")
            return code.strip() or None
        if text.startswith("STATUS_WAIT"):
            return None
        if text.startswith("STATUS_CANCEL"):
            raise SmsBowerError("hoạt động đã bị huỷ")
        return None

    def cancel(self, activation_id: str) -> None:
        """Huỷ và hoàn tiền — luôn gọi khi bỏ dở, nếu không sẽ bị trừ tiền."""
        try:
            self.set_status(activation_id, 8)
        except SmsBowerError:
            pass

    def finish(self, activation_id: str) -> None:
        try:
            self.set_status(activation_id, 6)
        except SmsBowerError:
            pass
