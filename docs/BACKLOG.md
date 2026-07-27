# Backlog — việc còn lại sau đợt rà soát

Nguồn: rà soát 4 mặt (UX, front-end, browser flow, HTTP flow) bằng 59 agent,
55 phát hiện thô → **37 phát hiện đã xác minh**. Ngày 2026-07-26.

Mục đã sửa nằm ở cuối. Mục còn lại xếp theo mức độ.

---

## Đánh giá tổng thể

Cả hai flow **chạy đúng ở quy mô demo** (browser 5/5, HTTP 8/8 live) nhưng
**chưa sẵn sàng cho quy mô 50–200 job** mà UI đang quảng cáo. Điểm yếu chung:
đường happy path được làm kỹ, đường hỏng thì mỏng — nhiều nhánh lỗi lặng lẽ đốt
hết deadline thay vì báo và thoát.

---

## Cách retry account lỗi (đã kiểm chứng live 5/5)

Account đăng ký nửa chừng có hai trạng thái, đo bằng URL đích của GET authorize
(`classify_landing`):

- **`landing=login`** (`/log-in/password`): account đủ hồ sơ, có mật khẩu. Đăng
  nhập bằng `password/verify`. Nếu bật 2FA → dừng ở `mfa_challenge`, vượt bằng
  TOTP đã lưu (`mfa/verify` cần `{type:"totp", code, id}`, `id` là đuôi
  `/mfa-challenge/<id>`). **reg_mode=http** làm được trọn vẹn.
- **`landing=otp`** (`/email-verification`): đây là passwordless login. HTTP phải
  gọi **Resend** để server phát *login code*, rồi validate trên cùng session;
  callback OAuth được trả trực tiếp. Gọi Send tạo *verification code* và đẩy sai
  sang onboarding `/about-you` rồi `/auth/login_with`.

Fallback là lựa chọn theo batch, mặc định tắt và chạy đối xứng sang engine còn
lại. Live HTTP thuần đã đạt 8/8 với fallback tắt; lỗi ngoài như rate limit,
mailbox hoặc Cloudflare vẫn phải được báo nguyên nhân, không được fallback che.

Mật khẩu **tài khoản** ≠ mật khẩu **hộp thư**. Combo Hotmail chứa mật khẩu hộp
thư; mật khẩu ChatGPT do tool sinh và ghi vào DB (`jobs.password`) ngay khi
`user/register` trả 200. Retry phải nộp mật khẩu account (qua
`password_override`), không phải mật khẩu combo — nếu không `password/verify`
trả 401. Với account `landing=otp` cũ (mất mật khẩu account từ session trước),
đăng nhập passwordless nên không cần.

OTP: OpenAI gửi **hai** loại mail cùng lúc — "verification code" (cho
`email-otp/validate`) và "login code" (passwordless). Tiêu đề có cả tiếng Việt
(`browser_locale=vi-VN`): "Mã xác minh…" / "Mã đăng nhập…". Chọn nhầm loại →
401 → resend → 429. `otp_kind()` phân loại cả hai ngôn ngữ.

---

## CAO — nên làm trước

### 1. ✅ ĐÃ SỬA — Account nửa chừng cứu được, mật khẩu ghi ngay khi register
`gpt_reg/phases/http_reg.py`, `gpt_reg/signup.py`, `gpt_reg/mail/outlook.py`

Mật khẩu account ghi vào DB ngay khi `user/register` trả 200 (`account_created`).
Đường login/retry hoàn chỉnh: routing theo `classify_landing`, vượt 2FA, thử
create_account khi chưa ra session. Session file **gộp** thay vì ghi đè (trước
đây một lần retry không-2FA xoá mất `mfa_secret` → account mất vĩnh viễn). Xem
mục "Cách retry account lỗi" ở trên. Còn lại: cancel-check ở create_account
(mục 12).

### 2. FLOW_TIMEOUT không phải ngân sách wall-clock
`gpt_reg/phases/browser/__init__.py`

`bootstrap` nằm **ngoài** deadline 300s, còn `fill_about_you` (60s),
`wait_session_cookie` (60s), `poll_code` (180s) là timeout riêng **cộng thêm**.
Một job browser có thể giữ slot worker và ~300 MB RAM hơn 12 phút, trong khi nút
Stop không cắt được các timeout con.

**Hướng sửa:** một deadline duy nhất tính từ đầu `run()`, truyền xuống mọi bước
con dưới dạng thời gian còn lại.

---

## TRUNG BÌNH — UX/quan sát

### 3. Không có tín hiệu tiến độ bên trong một job
Job hiện `đang chạy…` suốt 60–300s, không có thời gian đã trôi, không có bước
hiện tại — **kẹt và chậm trông giống hệt nhau**. Dữ liệu đã có sẵn: `started_at`
nằm trong payload, worker đang log đúng tên màn hình.

**Hướng sửa:** hiện đồng hồ đếm từ `started_at`; đẩy màn hình hiện tại vào một
cột `stage` trong bảng jobs và hiện trên hàng.

### 4. Log Activity không gắn job_id
Sự kiện SSE **có** `job_id` nhưng client vứt đi khi ghi vào stream chung. Từ ~3
luồng trở lên không thể biết dòng nào của account nào.

**Hướng sửa:** thêm tiền tố email/8 ký tự job_id vào mỗi dòng ở chế độ "tất cả".

### 5. Xoá không hỏi lại, không có đường lấy lại
`Xoá xong` / `Xoá hết` xoá vĩnh viễn account đã thu được, hai nút xám giống nhau
cạnh nhau, không confirm, và sau đó không tải lại được từ UI.

**Hướng sửa:** confirm cho `Xoá hết`; hoặc tự xuất file trước khi xoá.

### 6. Bàn phím và screen reader
Nút `Dừng` là `<span>` lồng trong `<button>` của hàng — không tab tới được, và
làm hỏng accessible name của hàng. Trạng thái job chỉ truyền qua ký tự
`aria-hidden` + màu.

**Hướng sửa:** đưa `Dừng` ra ngoài hàng; thêm `aria-label` có trạng thái bằng chữ.

### 7. Tương phản chữ dưới chuẩn WCAG AA (đã sửa trong UI Vue)
UI mới dùng token `--text-subtle` trong `frontend/src/styles.css` với độ tương
phản cao hơn trên cả nền sáng và tối.

### 8. Re-render danh sách làm mất focus bàn phím
Vị trí cuộn đã giữ được, focus thì chưa — đang tab giữa danh sách mà có event là
mất chỗ.

### 9. Job có log đến qua SSE không bao giờ tải log đầy đủ từ server
`selectJob()` chỉ fetch khi `jobLines[jobId]` rỗng. Job đã có vài dòng từ SSE sẽ
mãi chỉ hiện mấy dòng đó, không thấy phần trước khi mở tab.

---

## TRUNG BÌNH — độ bền flow

### 10. Latch CONTINUE / PASSWORD_CREATE không có cap
Một khi `continue_clicked` / `register_attempted` bật, nếu màn hình lặp lại hợp
lệ thì vòng lặp chỉ `sleep` tới hết deadline. `same_screen` là bộ đếm duy nhất
nhưng nó **reset khi màn hình nhấp nháy một vòng** — và test đã chứng minh nhấp
nháy có thật (`/log-in-or-create-account` lúc input chưa render ra `UNKNOWN`).

**Hướng sửa:** đếm theo tổng số vòng ở mỗi screen thay vì số vòng liên tiếp.

### 11. about_you double-submit
Vòng resubmit 8s bắn lại submit khi lần đầu **vẫn đang bay** → gửi profile 2 lần.

**Hướng sửa:** chỉ resubmit khi form vẫn còn hiện và chưa có request nào pending.

### 12. Không có cancel-check ở bootstrap / sentinel / create_account / redirect
HTTP flow: Stop → thoát thật mất 30s–3 phút, xấu nhất vài phút.

### 13. Cache sdk.js ghi không nguyên tử
`gpt_reg/sentinel/quickjs.py` — nhiều job cùng tải và ghi đè một file; job khác
có thể đọc file đang ghi dở.

**Hướng sửa:** ghi ra file tạm rồi `os.replace` (nguyên tử).

### 14. Phase-2 và 2FA dựng session bằng vân tay đã đo là bị chặn
`gpt_reg/phases/http.py` dùng `CURL_IMPERSONATE_PRIMARY` (chrome145) trong khi
`docs/ANTIBOT.md` đo được chrome145 → CF 403. Đây là lý do 2FA hay dính
`CF challenge HTTP 403` rồi phải retry.

**Hướng sửa:** dùng chung `fingerprint.PROFILES[0]`.

### 15. Retry invalid_state 3 lần là quá ít, backoff không jitter
Tỷ lệ hỏng quan sát ~75% → còn ~42% trượt sau 3 lần. Backoff phẳng 1.5s, nhiều
luồng sẽ đồng pha. Rotation CF-403 và retry invalid_state **không kết hợp**: mỗi
lần retry lại bắt đầu từ `PROFILES[0]` và trả lại tới 30s ngủ mù.

### 16. Screenshot của các job đè lên nhau
Mọi job ghi vào cùng `runtime/artifacts` với tên theo giây → chạy song song thì
ảnh chẩn đoán ghi đè nhau.

**Hướng sửa:** thêm job_id vào tên file.

---

## THẤP

17. Retry là tất-cả-hoặc-không — API nhận `job_ids` nhưng UI không gửi.
18. `Retry lỗi` lúc batch đang chạy báo nhầm "Không có job để retry".
19. Không chặn chạy trùng: input không xoá sau Run, không dedupe.
20. Huỷ job không có phản hồi trên hàng cho tới khi worker chạm tới.
21. Error pane vỡ với chuỗi lỗi nhiều dòng, và bỏ qua định dạng xuất đang chọn.
22. Đếm và Success pane lệch nhau khi vượt 500 job.
23. Lỗi mạng tạm thời / 5xx giữa flow HTTP là terminal.
24. `phases/http.py` bỏ qua `trust_env`, không chuẩn hoá `socks5h`.
25. Session bootstrap lỗi vẫn mở tới hết job.

---

## Đã sửa — đợt tối ưu tự động (2026-07-27, batch 7/7)

| Vấn đề | Bằng chứng |
|---|---|
| Fallback chỉ một chiều và tự bật | fallback opt-in, mặc định tắt, hỗ trợ HTTP↔Browser; concurrency dùng trần Browser khi bật; `check_http_fallback` |
| Vân tay ưu tiên profile đã bị CF chặn | probe live 2026-07-27: safari18_0+chrome124 qua cả hai cửa → xếp lên đầu; `check_fingerprint` |
| HTTP flow Stop mất vài phút (mục 12) | thêm cancel-point ở login/mfa/finalize + should_cancel mỗi hop của follow_redirects; `check_http_cancel` |

## Đã sửa — đợt retry account (2026-07-27)

| Vấn đề | Bằng chứng |
|---|---|
| `landing=otp` gọi nhầm `password/verify` → 409 invalid_state | tách `login_kind` (password/otp); `check_http_login` |
| Chọn nhầm mã OTP (login vs verification, cùng giây, cả tiếng Việt) → 401→429 | `otp_kind()` + gom-rồi-xếp-hạng; `check_otp_kind` (7 kind + 4 chọn mail) |
| Retry nộp mật khẩu **hộp thư** vào `password/verify` → 401 | dùng `jobs.password` (mật khẩu account) qua `password_override`; `check_account_password` |
| Account có 2FA không retry được (kẹt `mfa_challenge`) | vượt bằng TOTP đã lưu, payload `{type,code,id}` dò bằng `probe_mfa_verify`; `check_mfa_challenge` |
| **Mất dữ liệu**: session file ghi đè → xoá `mfa_secret`, account mất vĩnh viễn | gộp + ghi nguyên tử; `check_session_merge` |
| Browser retry `landing=otp` kẹt 180s (mã gửi trước `otp_since`, resend chỉ chạy sau submit đầu) | bấm Resend khi vào OTP "nguội"; đo live MalanderOz |
| Landing OTP dùng Send nên nhận verification code, rơi sai vào `/about-you` và SPA | dùng Resend để nhận login code; validate trả callback trực tiếp; live HTTP thuần 8/8 |

## Đã sửa trong đợt trước

| Vấn đề | Bằng chứng |
|---|---|
| 1 dòng combo hỏng → 500 + job kẹt `queued` khoá nút Run vĩnh viễn | tái hiện được, nay validate trước khi ghi, trả 400 kèm số dòng |
| Log `<pre>` phình vô hạn (cap 4000 không tới DOM) | đổi sang buffer + gộp ghi DOM 120ms |
| Success pane race — 2 request chồng nhau làm mất account | đánh số thế hệ request |
| OTP re-poll sau 35s không bấm Resend → chờ chết 180s | thêm `click_resend` + làm mới mốc `since` |
| Nhánh TURNSTILE là code chết (`assert_not_blocked` giết trước) | tách Turnstile thành chặn mềm, test riêng |
| `hidden` không ẩn được ô stat (`.stat{display:flex}` thắng) | thêm rule chung + test bắt **mọi** element cùng lỗi |
| Profile Camoufox không bao giờ xoá (37 MB/job → 7.4 GB) | đo được, xoá ở `finally` + reap lúc khởi động + test |
| Thread pre-compute sentinel dùng chung curl session (crash cả process) | thread tự tạo và tự đóng session riêng |
| 1 Node worker/job (54 MB × 200 = 10.5 GB) | pool dùng chung 8 worker ≈ 139 MB |
| 429 rate limit không xử lý | backoff 30s×n, huỷ được giữa chừng |
| Job list giới hạn 50 → batch 200 chỉ thấy 50 | nâng 500, xuất thì không giới hạn |
| `jobLines` rò rỉ bộ nhớ | cap 400 dòng/job, 250 job |
| SSE rớt thì UI im lặng hiện dữ liệu cũ | pill "mất kết nối" + tự đồng bộ lại |
| Danh sách job nhảy mất vị trí cuộn | giữ scrollTop + gộp event 400ms |
| Màn `unknown` ngủ tới hết 300s | reload sau ~21s, bỏ cuộc sau ~63s |
| register 409 log "chuyển sang login" nhưng không làm gì → kẹt 300s | điều hướng thật sang `/log-in/password` |
| Nước SMS đã lưu không khôi phục, bị ghi đè mỗi lần Lưu | khôi phục từ settings |
| Không có kiểm tra cú pháp JS | `node --check` trong `check_ui_wiring` |
