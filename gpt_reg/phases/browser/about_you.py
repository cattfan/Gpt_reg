"""Màn hình /about-you: name + tuổi/ngày sinh, rồi đợi OAuth callback.

Port từ GSH `_fill_about_you` + cụm helper của nó. Hai điểm quan trọng so với
bản cũ:

  - Field được **phát hiện theo metadata DOM** (name/id/placeholder/aria-label/
    label/autocomplete/min/max) chứ không theo thứ tự Tab. OpenAI A/B test
    nhiều biến thể: `age`, `birth year`, một ô birthday đầy đủ, hoặc 3 ô
    month/day/year tách rời — gõ theo tab order sẽ nhập nhầm vào ô name.
  - Callback bắt bằng **response listener** chứ không phải request listener:
    request fire ngay lúc gửi, chưa biết server có set cookie session-token hay
    không. Đây là root cause của "callback URL captured" rồi vẫn timeout.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import date
from typing import Any, Callable

from gpt_reg.core.exceptions import BrowserPhaseError
from gpt_reg.phases.browser import i18n
from gpt_reg.phases.browser import screens as scr
from gpt_reg.phases.browser import selectors as sel

_LOAD_ERROR_MARKERS = (
    "oops, an error occurred",
    "networkerror when attempting to fetch",
    "failed to fetch",
    "network error",
)
_PAGE_ERROR_SELECTORS = (
    "text=/oops, an error occurred/i",
    "text=/networkerror when attempting to fetch/i",
    "text=/failed to fetch/i",
)
_SUBMIT_SELECTORS = sel.SUBMIT
_NAME_SELECTORS = ('input[name="name"]', 'input[autocomplete="name"]', 'input[id*="name" i]')
_BIRTH_TOKENS = ("birth", "bday", "year", "month", "day", "age")


def birth_values(birthdate: str) -> dict[str, str]:
    """`YYYY-MM-DD` → giá trị cho từng loại field."""
    y, m, d = (int(x) for x in birthdate.split("-"))
    today = date.today()
    age = today.year - y - ((today.month, today.day) < (m, d))
    return {
        "year": str(y),
        "month": str(m),
        "day": str(d),
        "age": str(max(18, min(age, 80))),
        "birthday": birthdate,
    }


def _to_number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_field(meta: dict[str, str]) -> str | None:
    """Xác định control này nhận giá trị gì, không dựa vào vị trí."""
    control_type = (meta.get("type") or "").lower()
    autocomplete = (meta.get("autocomplete") or "").lower()
    field_name = (meta.get("name") or "").strip().lower()
    if field_name == "age":
        return "age"
    if field_name in {"birthyear", "birth_year", "year"}:
        return "year"
    if field_name in {"birthday", "birthdate", "dob"}:
        return "birthday"

    haystack = (
        " ".join(
            str(meta.get(k) or "")
            for k in ("name", "id", "placeholder", "aria_label", "label", "autocomplete")
        )
        .lower()
        .replace("_", " ")
        .replace("-", " ")
    )
    if control_type in {"hidden", "checkbox", "password", "email", "submit", "button"}:
        return None
    if "name" in haystack and not any(
        t in haystack for t in ("birth", "age", "year", "month", "day")
    ):
        return None
    if autocomplete == "bday-year" or "birth year" in haystack or "birthyear" in haystack:
        return "year"
    if autocomplete == "bday-month" or "birth month" in haystack:
        return "month"
    if autocomplete == "bday-day" or "birth day" in haystack:
        return "day"
    if control_type == "date" or autocomplete == "bday":
        return "birthday"
    if re.search(r"\bage\b", haystack):
        return "age"
    if any(t in haystack for t in ("birthday", "birth date", "date of birth", "dob")):
        return "birthday"
    if re.search(r"\b(year|yyyy)\b", haystack):
        return "year"
    if re.search(r"\b(month|mm)\b", haystack):
        return "month"
    if re.search(r"\b(day|dd)\b", haystack):
        return "day"

    minimum = _to_number(meta.get("min") or "")
    maximum = _to_number(meta.get("max") or "")
    if control_type == "number":
        if (minimum is not None and minimum >= 1800) or (maximum is not None and maximum >= 1900):
            return "year"
        if maximum is not None and maximum <= 150:
            return "age"
        if minimum is not None and minimum >= 13 and (maximum is None or maximum <= 150):
            return "age"
    return None


async def collect_fields(page) -> list[tuple[Any, dict[str, str]]]:
    controls = page.locator("form input, form select")
    fields: list[tuple[Any, dict[str, str]]] = []
    for index in range(await controls.count()):
        control = controls.nth(index)
        try:
            if not await control.is_visible(timeout=300):
                continue
            meta: dict[str, str] = {}
            for attr, key in (
                ("type", "type"),
                ("name", "name"),
                ("id", "id"),
                ("placeholder", "placeholder"),
                ("aria-label", "aria_label"),
                ("autocomplete", "autocomplete"),
                ("min", "min"),
                ("max", "max"),
            ):
                meta[key] = (await control.get_attribute(attr)) or ""
            meta["tag"] = await control.evaluate("(el) => el.tagName.toLowerCase()")
            try:
                meta["label"] = await control.evaluate(
                    """(el) => {
                        const explicit = el.id
                            ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
                            : null;
                        const wrapping = el.closest('label');
                        return ((explicit || wrapping)?.textContent || '').trim();
                    }"""
                )
            except Exception:
                meta["label"] = ""
            fields.append((control, meta))
        except Exception:
            continue
    return fields


async def set_control(control, meta: dict[str, str], value: str) -> bool:
    """Điền 1 control rồi verify lại giá trị có thực sự vào không."""
    try:
        if meta.get("tag") == "select":
            selected = False
            for candidate in dict.fromkeys([value, str(int(value)) if value.isdigit() else value]):
                try:
                    await control.select_option(candidate, timeout=2000)
                    selected = True
                    break
                except Exception:
                    continue
            if not selected:
                return False
        else:
            for action in (
                lambda: control.click(timeout=2000),
                lambda: control.fill("", timeout=3000),
            ):
                try:
                    await action()
                except Exception:
                    pass
            try:
                await control.fill(value, timeout=3000)
            except Exception:
                await control.type(value, delay=30)
        for event in ("input", "change", "blur"):
            try:
                await control.dispatch_event(event)
            except Exception:
                pass
        actual = (await control.input_value()).strip()
        if actual == value:
            return True
        return actual.isdigit() and value.isdigit() and int(actual) == int(value)
    except Exception:
        return False


async def fill_birth_fields(page, birthdate: str, log: Callable[[str], None]) -> str:
    """Điền mọi field tuổi/ngày sinh đang hiện. Return biến thể form đã gặp."""
    values = birth_values(birthdate)
    filled: list[str] = []
    for control, meta in await collect_fields(page):
        kind = classify_field(meta)
        if not kind:
            continue
        value = values[kind]
        if kind == "birthday" and (meta.get("type") or "").lower() != "date":
            y, m, d = birthdate.split("-")
            value = f"{m}/{d}/{y}"
        if await set_control(control, meta, value):
            filled.append(kind)
            log(f"[browser] about-you: {kind}={value}")
    if not filled:
        log("[browser] WARN about-you: không tìm thấy field tuổi/ngày sinh")
        return "none"
    return "+".join(filled)


async def check_extras(page, *, log: Callable[[str], None]) -> None:
    """Tick checkbox TOS và chọn select không liên quan ngày sinh."""
    try:
        boxes = page.locator('input[type="checkbox"]')
        for i in range(await boxes.count()):
            box = boxes.nth(i)
            if await box.is_visible(timeout=300) and not await box.is_checked():
                await box.check(timeout=2000)
                log(f"[browser] about-you: checked checkbox #{i}")
    except Exception:
        pass
    try:
        selects = page.locator("select")
        for i in range(await selects.count()):
            select = selects.nth(i)
            if not await select.is_visible(timeout=300):
                continue
            fingerprint = " ".join(
                (await select.get_attribute(attr)) or ""
                for attr in ("name", "id", "aria-label", "autocomplete")
            ).lower()
            # Select ngày sinh đã do fill_birth_fields lo; chọn option đầu ở đây
            # sẽ ghi đè bằng giá trị sai.
            if any(t in fingerprint for t in _BIRTH_TOKENS):
                continue
            if await select.input_value():
                continue
            first = await select.evaluate(
                "(el) => { const o = [...el.options].filter(x => x.value); return o.length ? o[0].value : null; }"
            )
            if first:
                await select.select_option(first, timeout=2000)
                log(f"[browser] about-you: selected {first}")
    except Exception:
        pass


async def click_submit(page, *, log: Callable[[str], None], quiet: bool = False) -> bool:
    """Return True nếu bấm được nút submit.

    `quiet=True` cho các lần retry: form đã submit thì nút biến mất, đó là bình
    thường chứ không phải lỗi.
    """
    for s in _SUBMIT_SELECTORS:
        try:
            btn = page.locator(s).first
            if await btn.is_visible(timeout=800) and await btn.is_enabled(timeout=500):
                await btn.click(timeout=3000)
                log(f"[browser] about-you: clicked {s}")
                return True
        except Exception:
            continue
    try:
        buttons = page.locator("button")
        for i in range(await buttons.count()):
            btn = buttons.nth(i)
            if not (await btn.is_visible(timeout=300) and await btn.is_enabled(timeout=300)):
                continue
            text = ((await btn.text_content(timeout=500)) or "").strip()
            if text and not i18n.contains_any(text, i18n.NOT_SUBMIT_WORDS):
                await btn.click(timeout=3000)
                log(f"[browser] about-you: fallback clicked {text[:40]!r}")
                return True
    except Exception:
        pass
    if not quiet:
        log("[browser] WARN about-you: không click được nút submit")
    return False


async def form_visible(page) -> bool:
    return await scr.visible(page, scr.NAME_INPUT, timeout_ms=300)


async def detect_page_error(page) -> str | None:
    for s in _PAGE_ERROR_SELECTORS:
        try:
            el = page.locator(s).first
            if await el.is_visible(timeout=300):
                text = ((await el.text_content(timeout=500)) or "").strip()
                if text:
                    return text[:200]
        except Exception:
            continue
    try:
        body = ((await page.locator("body").text_content(timeout=500)) or "").strip()
        lowered = body.casefold()
        if any(m in lowered for m in _LOAD_ERROR_MARKERS):
            if "about-you" in (page.url or "") or "oops" in lowered:
                return body[:200]
    except Exception:
        pass
    return None


async def recover_page(page, *, log: Callable[[str], None], attempt: int) -> bool:
    log(f"[browser] about-you: recovery attempt {attempt}")
    try:
        btn = page.locator(i18n.TRY_AGAIN_BUTTON).first
        if await btn.is_visible(timeout=800):
            await btn.click(timeout=5000)
            log("[browser] about-you: clicked Try again")
            await asyncio.sleep(1.5)
            return True
    except Exception:
        pass
    try:
        await page.reload(wait_until="domcontentloaded", timeout=30_000)
        log("[browser] about-you: reloaded")
        await asyncio.sleep(1.0)
        return True
    except Exception as exc:
        log(f"[browser] about-you: reload failed {type(exc).__name__}: {exc}")
        return False


async def wait_ready(page, *, timeout_s: float = 45.0, log: Callable[[str], None]) -> None:
    """Đợi form /about-you tương tác được, tự phục hồi khi SPA load lỗi."""
    deadline = time.monotonic() + timeout_s
    attempts = 0
    blank_since: float | None = None
    while time.monotonic() < deadline:
        cur = page.url or ""
        if "chatgpt.com" in cur and "auth.openai.com" not in cur:
            return
        if "auth/error" in cur:
            raise BrowserPhaseError(f"error page: {cur}", step="about_you")
        if await form_visible(page):
            log("[browser] about-you: form ready")
            return

        page_err = await detect_page_error(page)
        if page_err:
            if attempts < 5:
                attempts += 1
                log(f"[browser] about-you: load error {page_err[:120]}")
                if await recover_page(page, log=log, attempt=attempts):
                    blank_since = None
                    continue
            raise BrowserPhaseError(
                f"/about-you không load được sau {attempts} lần: {page_err[:120]}",
                step="about_you",
            )

        if "about-you" in cur:
            if blank_since is None:
                blank_since = time.monotonic()
            elif time.monotonic() - blank_since >= 8.0 and attempts < 5:
                attempts += 1
                log("[browser] about-you: form trắng >8s — reload")
                if await recover_page(page, log=log, attempt=attempts):
                    blank_since = None
                    continue
        else:
            blank_since = None
        await asyncio.sleep(0.5)
    raise BrowserPhaseError(f"timeout {timeout_s}s đợi form /about-you", step="about_you")


async def fill_about_you(
    page,
    name: str,
    birthdate: str,
    log: Callable[[str], None],
    *,
    timeout_s: float = 60.0,
) -> str | None:
    """Điền + submit /about-you. Return callback URL nếu bắt được."""
    await wait_ready(page, timeout_s=min(45.0, max(15.0, timeout_s * 0.75)), log=log)
    log(f"[browser] about-you: fill name={name!r}")

    holder: dict[str, Any] = {}

    def _on_response(response) -> None:
        url = response.url
        if "chatgpt.com/api/auth/callback/openai" not in url or "code=" not in url:
            return
        if "url" in holder:
            return
        status = response.status
        if 200 <= status < 400:
            holder["url"] = url
            holder["status"] = status
            log(f"[browser] about-you: callback OK HTTP {status}")
        else:
            holder["error_status"] = status
            log(f"[browser] about-you: callback FAILED HTTP {status}")

    page.on("response", _on_response)
    try:
        name_sel = None
        for s in _NAME_SELECTORS:
            try:
                await page.wait_for_selector(s, state="visible", timeout=5000)
                name_sel = s
                break
            except Exception:
                continue
        if not name_sel:
            raise BrowserPhaseError("không tìm thấy name input trên /about-you", step="about_you")

        await page.click(name_sel, force=True, timeout=3000)
        await page.fill(name_sel, "")
        await page.type(name_sel, name, delay=80)
        await asyncio.sleep(0.2)

        variant = await fill_birth_fields(page, birthdate, log)
        log(f"[browser] about-you: birth variant={variant}")
        await asyncio.sleep(0.3)
        await check_extras(page, log=log)
        await click_submit(page, log=log)

        deadline = time.monotonic() + timeout_s
        next_retry_at = time.monotonic() + 8.0
        attempts = 1
        while time.monotonic() < deadline:
            if "error_status" in holder and "url" not in holder:
                raise BrowserPhaseError(
                    f"callback /api/auth/callback/openai HTTP {holder['error_status']}",
                    step="about_you",
                )
            if "url" in holder:
                # Cho cookie jar kịp commit trước khi caller poll cookies.
                await asyncio.sleep(0.8)
                return holder["url"]
            cur = page.url or ""
            if "auth/error" in cur:
                raise BrowserPhaseError(f"error page: {cur}", step="about_you")
            if "chatgpt.com" in cur and "auth.openai.com" not in cur:
                log("[browser] about-you → chatgpt.com")
                return holder.get("url") or cur
            try:
                btn = page.locator(i18n.ACCEPT_BUTTON).first
                if await btn.is_visible(timeout=200):
                    await btn.click(timeout=2000)
                    log("[browser] about-you: clicked modal accept")
            except Exception:
                pass
            if "about-you" in cur and time.monotonic() > next_retry_at and attempts < 5:
                attempts += 1
                next_retry_at = time.monotonic() + 8.0
                if await click_submit(page, log=log, quiet=True):
                    log(f"[browser] about-you: submit lại (lần {attempts})")
            await asyncio.sleep(0.5)
        log("[browser] WARN about-you: hết thời gian đợi callback")
        return holder.get("url")
    finally:
        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass


async def read_access_token_from_page(page) -> str | None:
    """Đọc access_token qua origin đã auth, trước khi đóng browser.

    curl handoff có thể bị 403 dù session hợp lệ, nên phải lấy token ở đây.
    """
    try:
        if "chatgpt.com" not in (page.url or ""):
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=12_000)
    except Exception:
        return None
    for _ in range(3):
        try:
            result = await page.evaluate(
                """async () => {
                    const r = await fetch('/api/auth/session', {
                        method: 'GET',
                        credentials: 'include',
                        headers: {'Accept': 'application/json'},
                    });
                    let data = null;
                    try { data = await r.json(); } catch (_) {}
                    return {status: r.status, data};
                }"""
            )
            data = (result or {}).get("data") or {}
            token = data.get("accessToken") if isinstance(data, dict) else None
            if isinstance(token, str) and token:
                return token
            return None
        except Exception:
            await asyncio.sleep(0.25)
    return None
