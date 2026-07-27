"""Browser phase — state machine theo màn hình auth của OpenAI.

Vòng lặp gọi `screens.detect_screen()` rồi dispatch sang handler tương ứng, thay
cho bản cũ đoán bước tiếp theo bằng URL substring + locator visibility. Bản cũ
không có nhánh nào chạy sau khi OTP được submit, nên flow đứng im tới hết
deadline 300s (DidatoBascetta11, HenniganSharpless849).
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import time
import uuid
from typing import Callable

from camoufox.async_api import AsyncCamoufox

from gpt_reg.browser import artifacts
from gpt_reg.browser.challenges import assert_not_blocked
from gpt_reg.browser.driver import playwright_proxy_dict
from gpt_reg.browser.fingerprint import (
    browser_launch_identity,
    materialize_browser_fingerprint,
)
from gpt_reg.core.context import RunContext
from gpt_reg.core.contracts import MailProvider
from gpt_reg.core.deadline import Deadline, DeadlineExceeded
from gpt_reg.core.exceptions import BrowserPhaseError, ChallengeBlockedError
from gpt_reg.fingerprint import device_id_for_seed
from gpt_reg.models import BrowserHandoff, SignupRequest
from gpt_reg.phases.browser import about_you as about
from gpt_reg.phases.browser import otp as otp_mod
from gpt_reg.phases.browser import passkey
from gpt_reg.phases.browser import profile as prof
from gpt_reg.phases.browser import register as reg
from gpt_reg.phases.browser import screens as scr
from gpt_reg.phases.browser.timing import FlowTimer

# Ngân sách cho TOÀN BỘ job, tính từ lúc bắt đầu — bao gồm cả bootstrap và mọi
# bước con. Trước đây 300s chỉ bao vòng drive nên tổng thực tế có thể >12 phút.
JOB_BUDGET_S = 300.0
STUCK_SNAPSHOT_EVERY = 60.0
# Màn hình lạ: reload sau ~21s, bỏ cuộc sau ~63s thay vì đốt hết 300s.
# Vòng lặp ngủ 0.7s nên số vòng ≈ giây / 0.7.
UNKNOWN_RELOAD_AFTER = 30
UNKNOWN_GIVE_UP_AFTER = 90


def _remove_profile_dir(profile_dir, log: Callable[[str], None]) -> None:
    """Xoá thư mục profile Camoufox sau khi job xong.

    Giữ lại khi `KEEP_BROWSER_PROFILES=1` để còn debug được phiên vừa chạy.
    """
    if os.getenv("KEEP_BROWSER_PROFILES", "").strip() in ("1", "true", "yes"):
        return
    try:
        shutil.rmtree(profile_dir, ignore_errors=True)
    except Exception as exc:
        log(f"[browser] không xoá được profile {profile_dir.name}: {type(exc).__name__}")


def reap_stale_profiles(profiles_dir, log: Callable[[str], None] | None = None) -> int:
    """Dọn profile mồ côi của tiến trình đã chết. Trả về số thư mục đã xoá.

    Gọi lúc khởi động: job bị kill giữa chừng sẽ để lại profile mà không ai xoá.
    """
    removed = 0
    try:
        for entry in profiles_dir.iterdir():
            if entry.is_dir() and entry.name.startswith("camoufox_"):
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
    except FileNotFoundError:
        return 0
    except Exception:
        return removed
    if removed and log:
        log(f"[browser] dọn {removed} profile mồ côi")
    return removed


class BrowserPhase:
    mode = "browser"

    async def run(
        self,
        ctx: RunContext,
        request: SignupRequest,
        mail: MailProvider,
        *,
        log: Callable[[str], None],
    ) -> BrowserHandoff:
        stored_fingerprint = request.browser_fingerprint
        if stored_fingerprint is None:
            stored_fingerprint = materialize_browser_fingerprint(request.fingerprint_seed)
        launch_config, launch_preset = browser_launch_identity(
            stored_fingerprint,
            expected_seed=request.fingerprint_seed,
        )
        device_id = device_id_for_seed(request.fingerprint_seed, "browser")

        password = request.password or secrets.token_urlsafe(12)[:16]
        proxy_mat = None
        if request.proxy:
            from gpt_reg.proxy.format import materialize_proxy

            proxy_mat = materialize_proxy(request.proxy)
        elif ctx.proxy_pool:
            proxy_mat = ctx.proxy_pool.acquire()

        proxy_kw = playwright_proxy_dict(proxy_mat) or {}
        job_id = uuid.uuid4().hex[:8]
        profile_dir = ctx.settings.profiles_dir / f"camoufox_{job_id}"
        profile_dir.mkdir(parents=True, exist_ok=True)

        cf = AsyncCamoufox(
            config=launch_config,
            fingerprint_preset=launch_preset,
            i_know_what_im_doing=True,
            headless=request.headless,
            persistent_context=True,
            user_data_dir=str(profile_dir),
            locale=ctx.settings.browser_locale,
            geoip=bool(proxy_mat) and ctx.settings.browser_geoip,
            proxy=proxy_kw or None,
        )

        deadline = Deadline(JOB_BUDGET_S)
        try:
            async with cf as browser_ctx:
                page = browser_ctx.pages[0] if browser_ctx.pages else await browser_ctx.new_page()
                await self._bootstrap(ctx, page, request, device_id=device_id, log=log)
                deadline.raise_if_expired("bootstrap")
                return await self._drive(
                    ctx, browser_ctx, page, request, mail,
                    password=password, deadline=deadline, log=log,
                )
        except DeadlineExceeded as exc:
            raise BrowserPhaseError(str(exc), step="deadline") from exc
        finally:
            # Mỗi profile ~37 MB và trước đây không bao giờ được xoá — chạy 200
            # job là 7.4 GB rác tích trong runtime/profiles. Session đã nằm trong
            # handoff/file session rồi nên profile không còn giá trị gì.
            _remove_profile_dir(profile_dir, log)

    async def _bootstrap(
        self,
        ctx: RunContext,
        page,
        request: SignupRequest,
        *,
        device_id: str,
        log: Callable[[str], None],
    ) -> None:
        logging_id = str(uuid.uuid4())
        for attempt in (1, 2):
            started = time.monotonic()
            try:
                await reg.goto_chatgpt(page, artifact_dir=ctx.artifact_dir, log=log)
                goto_done = time.monotonic()
                await reg.bootstrap(
                    page,
                    email=request.email,
                    device_id=device_id,
                    logging_id=logging_id,
                    artifact_dir=ctx.artifact_dir,
                    log=log,
                )
                log(
                    f"[timing] bootstrap goto {goto_done - started:.1f}s "
                    f"authorize {time.monotonic() - goto_done:.1f}s"
                )
                return
            except ChallengeBlockedError:
                raise
            except Exception as exc:
                transient = "SSL" in str(exc) or "EOF" in str(exc)
                if attempt == 2 or not transient:
                    raise
                log(f"[browser] WARN bootstrap transient: {exc} — retry")
                await asyncio.sleep(3.0)

    async def _drive(
        self,
        ctx: RunContext,
        browser_ctx,
        page,
        request: SignupRequest,
        mail: MailProvider,
        *,
        password: str,
        deadline: Deadline,
        log: Callable[[str], None],
    ) -> BrowserHandoff:
        timer = FlowTimer()
        try:
            return await self._drive_loop(
                ctx, browser_ctx, page, request, mail,
                password=password, deadline=deadline, timer=timer, log=log,
            )
        finally:
            # Số liệu phải in cả khi flow chết giữa chừng — đó chính là lúc cần nhất.
            timer.report(log)

    async def _drive_loop(
        self,
        ctx: RunContext,
        browser_ctx,
        page,
        request: SignupRequest,
        mail: MailProvider,
        *,
        password: str,
        deadline: Deadline,
        timer: FlowTimer,
        log: Callable[[str], None],
    ) -> BrowserHandoff:
        otp_seconds = 0.0
        otp_since = None
        consumed: set[str] = set()
        submission: otp_mod.OtpSubmission | None = None
        register_attempted = False
        login_attempted = False
        cold_otp_resent = False
        continue_clicked = False
        email_submitted = False
        callback_url: str | None = None
        last_screen: str | None = None
        same_screen = 0
        unknown_reloaded = False
        account_existed = False
        next_snapshot = time.monotonic() + STUCK_SNAPSHOT_EVERY

        while not deadline.expired():
            ctx.raise_if_cancelled("browser_drive")
            try:
                await assert_not_blocked(page, artifact_dir=ctx.artifact_dir, log=log)
            except ChallengeBlockedError:
                raise
            except Exception as exc:
                log(f"[browser] WARN check blocked: {exc}")

            detect_started = time.monotonic()
            screen = await scr.detect_screen(page)
            timer.record_detect(time.monotonic() - detect_started)
            timer.enter(screen)
            if screen != last_screen:
                log(f"[flow] screen={screen} url={(page.url or '').split('?')[0]}")
                last_screen, same_screen = screen, 0
            else:
                same_screen += 1

            if time.monotonic() > next_snapshot:
                next_snapshot = time.monotonic() + STUCK_SNAPSHOT_EVERY
                await self._snapshot(ctx, page, screen, log=log)

            if screen == scr.CHATGPT:
                return await self._finish(
                    browser_ctx,
                    page,
                    request,
                    callback_url=callback_url,
                    otp_seconds=otp_seconds,
                    account_existed=account_existed,
                    deadline=deadline,
                    log=log,
                )

            if screen == scr.AUTH_ERROR:
                raise BrowserPhaseError(f"auth error page: {page.url}", step="drive")

            if screen == scr.MFA_CHALLENGE:
                raise BrowserPhaseError(
                    "account yêu cầu 2FA — không phải flow đăng ký mới", step="mfa"
                )

            if screen == scr.TURNSTILE:
                if same_screen == 0:
                    log("[flow] Turnstile — đợi tự giải")
                if same_screen > 60:
                    raise BrowserPhaseError("Turnstile kẹt >60 vòng", step="turnstile")
                await asyncio.sleep(1.0)
                continue

            if screen == scr.EMAIL_ENTRY:
                if email_submitted:
                    if same_screen > 40:
                        raise BrowserPhaseError(
                            "màn nhập email không chuyển tiếp", step="email_entry"
                        )
                    await asyncio.sleep(0.5)
                    continue
                email_submitted = await reg.submit_email(page, request.email, log)
                await asyncio.sleep(1.5)
                continue

            if screen == scr.CONTINUE:
                if not continue_clicked:
                    continue_clicked = await reg.click_password_button(page, log)
                await asyncio.sleep(1.5)
                continue

            if screen == scr.PASSWORD_CREATE:
                if register_attempted:
                    await asyncio.sleep(1.0)
                    continue
                register_attempted = True
                if otp_since is None:
                    otp_since = otp_mod.utc_now()
                account_existed = await self._register(
                    page, request.email, password, ctx=ctx, log=log
                )
                await asyncio.sleep(1.0)
                continue

            if screen == scr.PASSWORD_LOGIN:
                # Account đã tồn tại (partial register lần chạy trước) — đăng nhập
                # bằng đúng password trong combo thay vì đăng ký lại.
                if login_attempted:
                    if same_screen > 40:
                        raise BrowserPhaseError(
                            "đăng nhập bằng password không được chấp nhận", step="login"
                        )
                    await asyncio.sleep(0.5)
                    continue
                login_attempted = True
                account_existed = True
                if otp_since is None:
                    otp_since = otp_mod.utc_now()
                log("[flow] account đã tồn tại — đăng nhập bằng password")
                await reg.set_password(page, password, log)
                await asyncio.sleep(1.5)
                continue

            if screen == scr.OTP:
                if otp_since is None:
                    otp_since = otp_mod.utc_now()
                # Đến thẳng màn OTP mà lần chạy này CHƯA register/login (account
                # đăng ký nửa chừng, landing=otp): mã đã được gửi ở lần điều
                # hướng TRƯỚC, tức trước `otp_since`, nên poll sẽ không bao giờ
                # thấy và kẹt đủ 180s (resend chỉ chạy sau lần submit đầu — mà ta
                # chẳng có mã nào để submit). Bấm Resend ngay để có mã mới hơn
                # `otp_since`. Đo thật trên MalanderOz7584@hotmail.com.
                if (
                    submission is None
                    and not cold_otp_resent
                    and not register_attempted
                    and not login_attempted
                ):
                    account_existed = True
                    cold_otp_resent = True
                    log("[flow] vào màn OTP nhưng chưa gửi mã lần này — bấm Resend lấy mã mới")
                    await otp_mod.click_resend(page, log)
                    otp_since = otp_mod.utc_now()
                    await asyncio.sleep(2.0)
                    continue
                if submission is not None:
                    rejected = await otp_mod.detect_rejection(page)
                    if rejected:
                        log(f"[flow] OTP bị từ chối: {rejected[:80]} — resend")
                        await otp_mod.click_resend(page, log)
                        submission = None
                        await asyncio.sleep(2.0)
                        continue
                    if await submission.escalate(page):
                        submission = None
                        # Escalate vừa bấm Resend — chỉ nhận mail từ lúc này trở
                        # đi, nếu không vòng poll sẽ quét lại toàn bộ mã cũ.
                        otp_since = otp_mod.utc_now()
                    await asyncio.sleep(0.5)
                    continue

                try:
                    selector = await reg.wait_otp_form(page, timeout_s=10.0, log=log)
                except BrowserPhaseError:
                    await asyncio.sleep(0.5)
                    continue
                code, waited = await otp_mod.poll_code(
                    mail,
                    email=request.email,
                    since=otp_since,
                    timeout_s=request.otp_timeout_seconds,
                    poll_interval_s=request.otp_poll_interval_seconds,
                    log=log,
                    consumed=consumed,
                    should_cancel=ctx.should_cancel,
                )
                otp_seconds += waited
                log(f"[flow] OTP nhận sau {waited:.1f}s")
                await otp_mod.submit(page, code, log, selector=selector)
                submission = otp_mod.OtpSubmission(code, log)
                await asyncio.sleep(2.0)
                continue

            if screen == scr.PASSKEY_ENROLL:
                log("[flow] passkey enrollment — bỏ qua")
                await passkey.skip_passkey(page, log=log)
                await asyncio.sleep(1.5)
                continue

            if screen == scr.ABOUT_YOU:
                submission = None
                callback_url = await about.fill_about_you(
                    page, request.name, request.birthdate, log,
                    timeout_s=deadline.slice(60.0, minimum=10.0),
                )
                return await self._finish(
                    browser_ctx,
                    page,
                    request,
                    callback_url=callback_url,
                    otp_seconds=otp_seconds,
                    account_existed=account_existed,
                    deadline=deadline,
                    log=log,
                )

            # screen == unknown. Trước đây chỉ ngủ tiếp cho tới khi hết 300s —
            # nếu SPA render hỏng hoặc OpenAI đổi màn hình thì cả job cháy sạch
            # thời gian mà không thử gỡ. Reload một lần thường đủ để SPA dựng lại;
            # nếu vẫn không nhận ra thì thoát sớm để retry còn kịp giá trị.
            if same_screen == UNKNOWN_RELOAD_AFTER and not unknown_reloaded:
                unknown_reloaded = True
                log(f"[flow] màn hình lạ {same_screen} vòng — reload thử gỡ")
                await self._snapshot(ctx, page, "unknown_before_reload", log=log)
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=30_000)
                except Exception as exc:
                    log(f"[flow] reload lỗi: {type(exc).__name__}: {exc}")
            elif same_screen >= UNKNOWN_GIVE_UP_AFTER:
                await self._snapshot(ctx, page, "unknown_giveup", log=log)
                raise BrowserPhaseError(
                    f"không nhận ra màn hình sau {same_screen} vòng "
                    f"(url={page.url}) — xem screenshot trong runtime/artifacts",
                    step="unknown_screen",
                )
            await asyncio.sleep(0.7)

        await self._snapshot(ctx, page, last_screen or "unknown", log=log)
        raise BrowserPhaseError(
            f"hết ngân sách {JOB_BUDGET_S:.0f}s (đã dùng {deadline.elapsed:.0f}s, "
            f"màn cuối={last_screen}, url={page.url})",
            step="drive",
        )

    async def _finish(
        self,
        browser_ctx,
        page,
        request: SignupRequest,
        *,
        callback_url: str | None,
        otp_seconds: float,
        account_existed: bool,
        deadline: Deadline,
        log: Callable[[str], None],
    ) -> BrowserHandoff:
        cookies = await prof.wait_session_cookie(
            browser_ctx, page, timeout_s=deadline.slice(60.0, minimum=10.0), log=log
        )
        access = await about.read_access_token_from_page(page)
        return BrowserHandoff(
            cookies=cookies,
            callback_url=callback_url,
            otp_seconds=otp_seconds,
            authenticated_email=request.email,
            access_token=access,
            registration_outcome="account_exists" if account_existed else "success",
        )

    async def _register(self, page, email: str, password: str, *, ctx, log) -> bool:
        result = await reg.register_user(page, email=email, password=password, log=log)
        status = result.get("status")
        body = result.get("body") or {}
        if status == 200:
            cont = body.get("continue_url") if isinstance(body, dict) else None
            log(f"[flow] register OK → continue_url={cont}")
            # Account đã tồn tại từ đây — ghi mật khẩu trước khi làm OTP.
            ctx.account_created(password)
            if cont:
                if cont.startswith("/"):
                    cont = f"https://auth.openai.com{cont}"
                try:
                    await page.goto(cont, wait_until="domcontentloaded", timeout=30_000)
                except Exception as exc:
                    log(f"[flow] mở continue_url lỗi: {type(exc).__name__}: {exc}")
            return False
        body_str = json.dumps(body) if isinstance(body, dict) else str(body or "")
        if status == 409 or any(k in body_str.lower() for k in ("already", "exists")):
            # Chỉ log "chuyển sang login" là chưa đủ: SPA vẫn đứng ở màn tạo mật
            # khẩu, `register_attempted` đã bật nên vòng lặp rơi vào nhánh chờ
            # suông tới hết 300s. Phải tự điều hướng để classifier thấy
            # `password_login` và nhánh đăng nhập mới chạy.
            log("[flow] account đã tồn tại — điều hướng sang màn đăng nhập")
            try:
                await page.goto(
                    "https://auth.openai.com/log-in/password",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
            except Exception as exc:
                log(f"[flow] điều hướng login lỗi: {type(exc).__name__}: {exc}")
            return True
        raise BrowserPhaseError(f"register failed HTTP {status}: {body_str[:200]}", step="register")

    async def _snapshot(self, ctx: RunContext, page, screen: str, *, log) -> None:
        try:
            path = await artifacts.screenshot(page, ctx.artifact_dir, f"stuck_{screen}")
            log(f"[flow] snapshot screen={screen} url={page.url} → {path}")
        except Exception as exc:
            log(f"[flow] snapshot lỗi: {type(exc).__name__}: {exc}")


async def run_browser_phase(
    ctx: RunContext,
    request: SignupRequest,
    mail: MailProvider,
    *,
    log: Callable[[str], None],
) -> BrowserHandoff:
    return await BrowserPhase().run(ctx, request, mail, log=log)
