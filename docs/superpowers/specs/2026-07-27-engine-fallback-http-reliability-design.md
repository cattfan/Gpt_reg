# Engine fallback và độ tin cậy HTTP

## Mục tiêu

- Chế độ được chọn là engine chính và luôn chạy trước.
- Fallback là lựa chọn rõ ràng theo từng batch, mặc định tắt.
- Khi bật fallback, HTTP lỗi thì thử Browser; Browser lỗi thì thử HTTP.
- HTTP thuần không được âm thầm khởi chạy Browser.
- Luồng HTTP giữ một fingerprint nhất quán trong toàn bộ phiên OAuth.

## Contract

UI gửi `fallback_enabled` dưới dạng JSON boolean cho cả `/api/jobs/start` và
`/api/jobs/retry`. Server từ chối giá trị không phải boolean bằng HTTP 400.
`RegJobManager` truyền cờ này tới worker và chỉ chạy engine còn lại khi kết quả
engine chính lỗi, job chưa bị huỷ và fallback được bật.

Toggle chỉ hiện trong nhóm cấu hình engine, dùng nhãn ba ngôn ngữ. Toggle không
lưu SQLite: đây là quyết định của batch hiện tại, và mặc định tắt sau khi tải lại
ứng dụng để tránh chuyển engine ngoài ý muốn.

## HTTP và fingerprint

HTTP bootstrap được phép thử lần lượt các profile đã khai báo khi chưa tạo auth
state. Sau khi một profile qua Cloudflare và tạo session/state, mọi request OTP,
sentinel, create-account và OAuth callback phải dùng cùng profile đó. Không tự
ghi đè User-Agent, Client Hints, Accept-Language hay thứ tự header do
`curl_cffi` tạo.

Lỗi OTP phải giữ status và body đã rút gọn trong log/error để phân biệt mã sai,
`invalid_state`, rate limit và lỗi server. `invalid_state` được xử lý bằng một
phiên bootstrap mới; mã sai mới resend/poll OTP. Landing OTP passwordless phải
dùng endpoint Resend để nhận login code; dùng Send tạo verification code và đẩy
sai sang onboarding `/about-you`. Khi đúng loại OTP, validate trả OAuth callback
trực tiếp nên không đi qua SPA `/auth/login_with`.

## Trạng thái và log

Job giữ `reg_mode` là engine chính. Log ghi rõ `primary=<engine>` và khi có
fallback ghi `fallback=<engine>`, kèm nguyên nhân engine chính thất bại. Nếu cả
hai lỗi, error cuối phải chứa cả hai nguyên nhân, không che lỗi đầu tiên.

## Kiểm thử

- Unit/smoke: fallback tắt mặc định, fallback hai chiều, không fallback khi huỷ,
  API strict boolean, payload UI, i18n và fingerprint consistency.
- Live: chạy các account người dùng cung cấp ở HTTP thuần với fallback tắt;
  không lưu combo vào test/docs/artifact. Xác nhận log không chứa lần chạy
  Browser và session đầu ra hợp lệ.
- Browser live được dùng riêng để xác nhận Browser vẫn hoạt động; fallback hai
  chiều được kiểm chứng bằng test xác định, không cố tình phá mạng/account live.

## Giới hạn

Không thể bảo đảm mọi account luôn thành công trước rate limit, outage, mailbox
hoặc chính sách chống bot bên ngoài. Cam kết ở đây là mọi nhánh HTTP được hỗ trợ
chạy thuần HTTP, lỗi đúng nguyên nhân và không được Browser che lỗi.
