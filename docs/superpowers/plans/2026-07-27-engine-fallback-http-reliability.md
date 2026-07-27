# Engine Fallback và HTTP Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tạo fallback hai chiều có opt-in rõ ràng và làm luồng đăng ký HTTP chạy độc lập, giữ fingerprint nhất quán, báo lỗi có thể chẩn đoán.

**Architecture:** UI gửi một boolean `fallback_enabled` theo batch. API kiểm tra kiểu nghiêm ngặt rồi truyền qua `RegJobManager`; manager thực thi engine chính, sau đó mới thử engine đối diện nếu được bật. HTTP giữ nguyên profile sau bootstrap, phân loại lỗi OTP theo body và hoàn tất OAuth bằng HTTP callback thay vì Browser.

**Tech Stack:** FastAPI, Python threads, curl_cffi, Vue 3, TypeScript, vue-i18n, Vitest, script kiểm thử `test/check_*.py`.

---

### Task 1: Contract fallback hai chiều

**Files:**
- Modify: `test/check_http_fallback.py`
- Modify: `gpt_reg/web/jobs/reg_manager.py`
- Modify: `gpt_reg/web/server.py`
- Modify: `test/check_job_api.py`

- [x] **Step 1: Viết test đỏ** cho fallback mặc định tắt, HTTP -> Browser và Browser -> HTTP khi `fallback_enabled=True`, không fallback khi thành công hoặc bị huỷ.
- [x] **Step 2: Chạy** `python test/check_http_fallback.py` và xác nhận test thất bại với signature hiện tại.
- [x] **Step 3: Sửa manager**: bỏ `_AUTO_FALLBACK_HTTP_TO_BROWSER`, truyền `fallback_enabled` qua `start_batch`, `_worker`, `_run_one`; chọn engine đối diện bằng mapping `{"http": "browser", "browser": "http"}`.
- [x] **Step 4: Sửa API**: thêm parser JSON boolean nghiêm ngặt; thiếu field là `False`, chuỗi/number trả HTTP 400; truyền field cho start/retry.
- [x] **Step 5: Chạy** `python test/check_http_fallback.py` và `python test/check_job_api.py`, kỳ vọng đều `[ok]`.

### Task 2: Điều khiển fallback trên UI ba ngôn ngữ

**Files:**
- Modify: `frontend/src/views/RegistrationView.vue`
- Modify: `frontend/src/i18n.ts`
- Modify: `frontend/src/__tests__/views.spec.ts`

- [x] **Step 1: Viết test đỏ** xác nhận toggle mặc định tắt và payload start/retry chứa `fallback_enabled`; bật toggle đổi payload thành `true`.
- [x] **Step 2: Chạy** `npm test -- --run src/__tests__/views.spec.ts` trong `frontend`, kỳ vọng thất bại.
- [x] **Step 3: Thêm ref/toggle** `fallbackEnabled`, gửi boolean cho start/retry, và thêm nhãn vi/en/zh-CN mô tả fallback sang engine còn lại.
- [x] **Step 4: Chạy lại Vitest**, kỳ vọng pass.

### Task 3: Chẩn đoán và sửa HTTP thuần

**Files:**
- Modify: `test/check_http_login.py`
- Create hoặc Modify: `test/check_http_otp_errors.py`
- Modify: `gpt_reg/phases/http_reg.py`
- Modify khi cần: `gpt_reg/fingerprint.py`
- Modify khi cần: `test/check_fingerprint.py`

- [x] **Step 1: Viết test đỏ** cho phân loại OTP: wrong-code được resend; `invalid_state` không bị báo chung `OTP verify HTTP 409`; error giữ body đã khử dữ liệu nhạy cảm.
- [x] **Step 2: Chạy** các test HTTP/fingerprint liên quan và ghi nhận lỗi mong đợi.
- [x] **Step 3: Chạy probe live có kiểm soát** bằng account người dùng cung cấp, fallback tắt; chỉ log status, route, page type và body rút gọn, không ghi combo/token.
- [x] **Step 4: Sửa OTP/session flow theo bằng chứng**: re-bootstrap khi state hết hạn; landing OTP dùng Resend để nhận login code và lấy callback trực tiếp, tránh rơi sai vào onboarding `/auth/login_with`.
- [x] **Step 5: Kiểm tra fingerprint**: chỉ xoay trước khi auth state được tạo; profile/session/sentinel navigator phải khớp; không thêm identity header thủ công.
- [x] **Step 6: Chạy** `python test/check_http_login.py`, `python test/check_http_otp_errors.py`, `python test/check_fingerprint.py`, kỳ vọng `[ok]`.

### Task 4: Regression và live verification

**Files:**
- Modify khi phát hiện regression: các file thuộc Task 1-3

- [x] **Step 1: Chạy backend suite** bằng `python test/run_all.py`; kỳ vọng mọi check pass.
- [x] **Step 2: Chạy frontend suite và build** bằng `npm test -- --run` và `npm run build` trong `frontend`; kỳ vọng pass.
- [x] **Step 3: Chạy các account được cung cấp ở HTTP thuần**, `fallback_enabled=false`; xác nhận không có log Browser và session hợp lệ cho các account không bị chặn bởi dịch vụ ngoài.
- [x] **Step 4: Chạy một Browser live riêng** để bảo đảm engine Browser không regression. Fallback hai chiều đã được xác nhận bằng unit test, tránh cố tình làm hỏng live flow.
- [x] **Step 5: Tổng hợp tỷ lệ và lỗi bên ngoài** theo từng bước, không gọi là HTTP thành công nếu Browser đã tham gia.
