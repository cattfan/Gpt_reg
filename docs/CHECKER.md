# Check plan tài khoản

Tab **Check acc** đăng nhập bằng HTTP và đọc gói tài khoản từ
`/backend-api/accounts/check/v4-2023-04-27`.

## Định dạng input

Mỗi dòng dùng một trong hai dạng:

```text
mail|pass|2fa
mail|pass|2fa|email|mailpass|refresh_token|client_id
```

- `pass` là mật khẩu tài khoản ChatGPT, không phải mật khẩu hộp thư.
- `2fa` là TOTP secret base32; để trống nếu tài khoản không bật 2FA, nhưng vẫn giữ dấu `|`.
- Bốn field cuối là `fullmail` Outlook. Chỉ cần khi đăng nhập yêu cầu email OTP.

## Sử dụng

1. Mở tab **Check acc**, dán danh sách và chọn 1-200 luồng.
2. Bấm **Check plan**. Nút **Dừng** huỷ batch; **Retry lỗi** chạy lại các dòng lỗi tạm thời.
3. **Xuất** copy kết quả live theo dạng `email|plan|sub|2fa`.

Kết quả được lưu trong bảng SQLite `checks`. Trạng thái `live` nghĩa là đăng nhập và
đọc plan thành công; `die` là sai thông tin/không tồn tại; `onboarding` là tài khoản
passwordless chưa hoàn tất, nên luồng HTTP không thể đọc plan.
