# Gpt_reg

Camoufox + proxy + ChatGPT signup (browser) với Outlook/Hotmail Graph OTP.

## Yêu cầu

**Python 3.11 bắt buộc.** Trên 3.12+ (đặc biệt 3.14) `curl_cffi`/`cffi` chưa có
wheel → HTTP phase và MFA phase không chạy được. `setup.bat` tạo venv bằng
`py -3.11` vào thư mục `.venv311` và dừng nếu không tìm thấy Python 3.11.

**Node.js 22+ bắt buộc.** `setup.bat` dùng Node để build Web UI Vue/Tailwind.
Reg mode `http` cũng dùng sentinel QuickJS (chạy sdk.js thật của OpenAI) để qua
deep anti-bot. Setup dừng ngay nếu thiếu Node hoặc frontend build lỗi.

## Setup (Windows)

```bat
setup.bat
```

Sau setup, mở UI:

```bat
start.bat
```

(`start.bat` — chỉ Web UI tại http://127.0.0.1:2023/, giải phóng port nếu bị chiếm.)

Web UI hỗ trợ Tiếng Việt, English và 简体中文, có light/dark mode và bố cục
responsive cho desktop/mobile. Chi tiết phát triển: **[`docs/WEB_UI.md`](docs/WEB_UI.md)**.

Web UI không có lớp đăng nhập riêng và chỉ bind trên loopback (`127.0.0.1`,
`localhost` hoặc `::1`). CLI dừng ngay khi cấu hình host ngoài loopback.

## CLI

```bat
.venv311\Scripts\gpt-reg migrate
.venv311\Scripts\gpt-reg smoke
.venv311\Scripts\gpt-reg mail-test --combo "email|pass|refresh|client-id"
.venv311\Scripts\gpt-reg signup --combo-file runtime\live_combo.txt --with-2fa
.venv311\Scripts\gpt-reg signup --combo-file runtime\live_combo.txt --reg-mode http --with-2fa
.venv311\Scripts\gpt-reg enable-2fa -f runtime\sessions\<email>.json
.venv311\Scripts\gpt-reg web
```

Dùng `--combo-file` thay `--combo` để combo không lọt vào PowerShell history.

`--reg-mode`: `browser` (Camoufox, mặc định, ~75–85s) hoặc `http` (curl_cffi +
sentinel, nhanh hơn nhưng phụ thuộc Cloudflare/Node).

## Xuất kết quả (Web UI)

Card Success có 3 định dạng (segmented control):

- **Combo** — `email|password|totp_secret`
- **+Mail** — `email|password|totp_secret|email|mail_password|refresh_token|client_id`
- **JSON** — mảng object đầy đủ

`totp_secret` là base32 để người mua tự sinh mã.

Exit codes: `0` OK, `1` error, `2` CAPTCHA/phone block.

## Đa luồng

Chọn số luồng trong UI: 1, 2, 5, 10, 20, 50, 100, 200.

Trần khác nhau theo chế độ vì chi phí mỗi job khác hẳn:

| Chế độ | Trần | Lý do |
|--------|------|-------|
| `browser` | 10 | mỗi Camoufox ~300 MB RAM |
| `http` | 200 | mỗi session curl_cffi ~10 MB |

Vượt trần thì bị ép về mức tối đa, không báo lỗi.

## Check plan tài khoản

Tab **Check acc** nhận `mail|pass|2fa` hoặc `mail|pass|2fa|fullmail`, đăng nhập
bằng HTTP và hiển thị plan Free/Plus/Pro/Team/... Hướng dẫn định dạng, trạng thái và
xuất kết quả: **[`docs/CHECKER.md`](docs/CHECKER.md)**.

## Chống phát hiện bot

Vân tay, IP, sentinel và chuỗi request HTTP đúng: xem **[`docs/ANTIBOT.md`](docs/ANTIBOT.md)**.
Đọc trước khi sửa `fingerprint.py`, `phases/http_reg.py` hoặc `sentinel/`.

## Browser phase

`gpt_reg/phases/browser/` là state machine theo màn hình auth
(`screens.detect_screen` → dispatch), port từ `privateGSH/browser_phase.py`.

| Screen | Handler |
|--------|---------|
| `chatgpt` | đợi session cookie → handoff |
| `about_you` | điền name + tuổi/ngày sinh, bắt OAuth callback |
| `otp` | poll mail, submit, leo thang 10s/18s/25s/35s |
| `password_create` | POST `/api/accounts/user/register` |
| `password_login` | account đã tồn tại → đăng nhập bằng password trong combo |
| `continue` | click "Continue with password" |
| `passkey_enroll` | skip |
| `turnstile_challenge` | đợi tự giải (tối đa 60 vòng) |
| `mfa_challenge` / `auth_error` | fail nhanh |

Khi kẹt, screenshot được ghi vào `runtime/artifacts/` mỗi 60s.
