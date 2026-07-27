# Fingerprint identity cố định cho HTTP và Browser

## Mục tiêu

- Mỗi job có một identity riêng, ổn định qua retry và qua fallback engine.
- Batch 200 job có 200 identity ID và device ID khác nhau, dù số TLS preset HTTP hữu hạn.
- HTTP dùng đúng fingerprint tầng mạng do `curl_cffi` tạo; không tự dựng lại header nhận dạng.
- Browser dùng preset Firefox thực được đóng gói cùng Camoufox, giữ nguyên Canvas, Audio,
  font, màn hình, navigator và WebGL của profile qua các lần chạy.
- Các trường liên quan phải tương thích theo bộ; không ghép độc lập OS, UA, GPU và màn hình.
- Cấu hình không hợp lệ phải fail-fast, không âm thầm trả về profile mặc định.

## Phạm vi

Thay đổi chỉ bao gồm bộ chọn fingerprint, dữ liệu job trong SQLite, HTTP bootstrap,
Sentinel navigator và cấu hình khởi chạy Camoufox. Fallback engine vẫn là tùy chọn; chỉ lỗi
transport/browser engine được phép kích hoạt nó, còn lỗi auth/mail tiếp tục fail-fast.

HTTP không thể cung cấp Canvas, WebGL, font hay media devices thật vì không chạy JavaScript
trình duyệt. Vì vậy “identity HTTP” được định nghĩa chính xác là TLS/HTTP2/header preset,
cookie jar, device ID và navigator tối thiểu dùng trong Sentinel. Không tuyên bố 200 TLS
fingerprint khác nhau khi thư viện chỉ có một số preset đã kiểm chứng.

## Mô hình identity

Mỗi job lưu ba trường trong bảng `jobs`:

- `fingerprint_seed`: seed 128-bit sinh bằng CSPRNG, không đổi khi retry.
- `fingerprint_profile`: tên HTTP preset chính được chọn từ seed.
- `fingerprint_data`: JSON cấu hình Browser đã materialize; để trống cho tới khi Browser
  thực sự được dùng.

Job mới được cấp seed trước khi worker chạy. Job cũ chưa có seed được backfill một lần bằng
giá trị SHA-256 ổn định từ job ID rồi ghi lại SQLite. Seed không được phát sinh lại trong
phase hoặc sau lỗi.

`identity_id` dùng trong log là SHA-256 rút gọn từ seed, không in seed thô. Device ID HTTP
được dẫn xuất bằng UUIDv5 từ seed và purpose, nhờ đó khác nhau giữa job nhưng ổn định khi
retry. Các nonce/state OAuth vẫn sinh mới theo từng lần bootstrap vì đó không phải thuộc
tính fingerprint.

## HTTP identity

Danh sách HTTP chỉ chứa các tên canonical đã được probe thành công với bản `curl_cffi`
đang khóa trong dự án; alias trùng TLS preset bị loại. Mỗi `HttpProfile` chứa tên,
`impersonate`, UA/platform thực tế do request local của `curl_cffi` xác nhận và nhóm
navigator tương thích.

Profile chính được chọn bằng rendezvous hashing từ `fingerprint_seed`. Khi bootstrap chưa
tạo auth state mà gặp đúng lỗi TLS hoặc Cloudflare 403, candidate order bắt đầu từ profile
đã chọn rồi đi qua phần còn lại theo thứ tự xác định. Sau khi một profile thành công, toàn
bộ CSRF, OAuth, OTP, Sentinel và callback của phiên phải dùng đúng session/profile đó.
Không xoay profile vì lỗi auth, mail, OTP, rate limit hoặc lỗi ứng dụng.

`get_profile()` phải ném `ValueError` với tên không tồn tại. Không có nhánh fallback về
profile đầu tiên. Việc tạo session cũng kiểm tra profile thuộc registry hiện hành.

Sentinel navigator lấy UA, platform và Client Hints từ chính `HttpProfile`. CPU/RAM được
chọn theo một tuple hợp lệ của platform bằng seed và giữ ổn định. Header nhận dạng tiếp tục
để `curl_cffi` tự gửi nhằm giữ cả giá trị lẫn thứ tự header đúng với TLS preset.

## Browser identity

Browser dùng `fingerprint-presets-v150.json` của Camoufox hiện hành. Preset được chọn xác
định từ seed trên toàn bộ candidate hợp lệ, không gọi lựa chọn ngẫu nhiên mỗi lần launch.
Toàn bộ preset được chuyển thành cấu hình Camoufox một lần, gồm navigator, OS, màn hình,
WebGL, font, speech voices và các seed noise Canvas/Audio/font spacing; cấu hình sau khi
materialize được lưu vào `jobs.fingerprint_data`. Payload chứa commitment SHA-256 của seed;
Browser phase phải đối chiếu commitment trước mỗi launch để phát hiện dữ liệu bị gắn nhầm job.

Retry hoặc fallback sang Browser đọc lại cấu hình đã lưu, không tạo preset/noise mới.
Các khóa timezone, locale, geolocation và WebRTC IP không nằm trong dữ liệu cố định; chúng
được Camoufox suy ra theo proxy/runtime hiện tại. Như vậy phần phần cứng ổn định nhưng vị trí
mạng không mâu thuẫn với proxy.

Nếu bundle preset bị thiếu, preset index không hợp lệ hoặc JSON trong SQLite hỏng, phase
Browser dừng với lỗi cấu hình rõ ràng. Không chuyển ngầm sang BrowserForge synthetic.

## SQLite và tương thích dữ liệu

Schema tăng lên v5 và thêm ba cột nullable để migrate được database cũ. Repository có thao
tác cấp identity nguyên tử dưới write lock: chỉ ghi seed/profile khi đang trống và luôn kiểm
tra profile chính đúng là profile dẫn xuất từ seed. Hai worker
không thể cấp hai identity khác nhau cho cùng job.

Khi xóa job, identity đi theo job. Retry riêng lẻ giữ nguyên cả ba trường. API danh sách job
không trả `fingerprint_seed` hoặc toàn bộ `fingerprint_data`; chỉ có thể trả tên profile và
identity ID nếu sau này UI cần hiển thị.

## Log và lỗi

Đầu mỗi attempt ghi một dòng dạng:

```text
[fingerprint] identity=12ab34cd56ef engine=http profile=chrome124
```

Khi HTTP đổi candidate trước auth state, log ghi profile cũ, profile mới và nguyên nhân
`tls` hoặc `cf_block`. Không ghi seed, cấu hình đầy đủ, proxy credential hay token.

Các lỗi cấu hình dùng thông báo riêng: profile không hỗ trợ, preset bundle thiếu, dữ liệu
Browser hỏng. Chúng không được phân loại thành lỗi auth/mail và không kích hoạt xoay HTTP
profile.

## Kiểm thử

- Unit: cùng seed cho cùng identity/profile/device ID; 200 seed cho 200 identity/device ID;
  phân phối trên pool HTTP; profile lạ fail-fast; candidate order xác định.
- Unit: profile HTTP khớp UA, Client Hints và platform do `curl_cffi` thật gửi tới socket
  local; không có header nhận dạng tự dựng.
- Unit: Browser cùng seed materialize cùng preset và noise; retry đọc nguyên cấu hình từ
  SQLite; timezone/geolocation không bị đóng cứng; JSON hỏng fail-fast.
- Migration: database v4 lên v5 giữ job cũ, cấp identity một lần và retry không đổi.
- Integration: HTTP bootstrap giữ profile sau khi có auth state; fallback hai chiều dùng
  cùng identity seed; lỗi auth/mail không làm xoay fingerprint.
- Smoke Browser: mở Camoufox, đọc navigator/screen/WebGL hai lần từ cùng cấu hình và xác
  nhận các trường fingerprint bằng nhau.

Live probe Cloudflare là kiểm tra thủ công riêng vì kết quả phụ thuộc thời điểm, proxy và
chính sách bên ngoài. Probe không ghi combo/tài khoản vào test, docs hoặc artifact.

## Tiêu chí hoàn thành

- 200 job mới có 200 identity ID và device ID khác nhau.
- Retry một job giữ nguyên seed, HTTP profile và Browser fingerprint data.
- HTTP không tự sửa identity headers và không xoay profile sau khi auth state tồn tại.
- Browser dùng preset thực của Camoufox, không rơi về BrowserForge khi lỗi.
- Toàn bộ `test/check_*.py` pass; smoke Browser xác nhận fingerprint ổn định.
