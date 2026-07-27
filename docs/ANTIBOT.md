# Chống phát hiện bot — vân tay, IP, sentinel

Tài liệu cho AI agent làm việc tiếp. Mọi con số ở đây là **đo thực tế**, không
phải suy đoán; mỗi mục đều kèm script để đo lại khi OpenAI/Cloudflare đổi luật.

Cập nhật: 2026-07-26.

---

## 1. Nguyên tắc gốc: vân tay là một BỘ, không phải từng mảnh

Ba lớp phải kể **cùng một câu chuyện**:

| Lớp | Ai quyết định | Sai thì sao |
|-----|---------------|-------------|
| TLS/HTTP2 (JA3, ALPN, thứ tự extension) | `curl_cffi` qua `impersonate` | CF chặn 403 |
| Header nhận dạng (`User-Agent`, `sec-ch-ua`, `Sec-Fetch-*`, thứ tự header) | **`curl_cffi` tự gửi** | Lệch TLS → bị flag |
| `navigator.*` trong sdk.js (sentinel) | `fingerprint.navigator_payload()` | Deep validation trượt → **silent-drop OTP** |

### Sai lầm đã mắc và cách phát hiện

**Tôi từng đặt TLS = `chrome131` nhưng vẫn gửi `User-Agent: Chrome/145`.** Chrome
thật luôn khai cùng một phiên bản ở cả ba chỗ.

**Nặng hơn: tự dựng header nhận dạng là phá vân tay.** `curl_cffi` khi
`impersonate` đã gửi trọn bộ header — đúng giá trị, **đúng thứ tự**. Ghi đè bằng
dict Python làm hỏng thứ tự đó.

Đo trên `chatgpt.com/auth/login`, cùng `impersonate=chrome131`:

| Header | Kết quả |
|--------|---------|
| để `curl_cffi` tự gửi | **200** |
| tự dựng dict | **403** |

Ba thứ không thể chép tay đúng (chạy `test/probe_default_headers.py` để thấy):

1. **Thứ tự header là vân tay.** Chrome gửi
   `sec-ch-ua → sec-ch-ua-mobile → sec-ch-ua-platform → Upgrade-Insecure-Requests
   → User-Agent → Accept → Sec-Fetch-* → Accept-Encoding → Accept-Language → Priority`.
2. **Nhãn "Not A Brand" đổi mỗi bản Chrome:** `Not_A Brand` (120) →
   `Not-A.Brand` (124) → `Not.A/Brand` (136) → `Not:A-Brand` (145). Thứ tự brand
   cũng đảo. Chrome 120 **không** gửi `Priority`, 124+ có.
3. **Mặc định của `curl_cffi` là macOS**, không phải Windows.

### Quy tắc hiện tại

`gpt_reg/phases/http_reg.py` chỉ thêm header **theo ngữ cảnh**:
`Referer`, `Origin`, `Content-Type`, `Accept` (khi cần JSON), datadog trace,
`oai-device-id`. Tuyệt đối không set `User-Agent` / `sec-ch-ua` / `Sec-Fetch-*` /
`Accept-Encoding`.

`_create_session()` **đính profile vào session** (`session.gpt_profile`), mọi hàm
đọc profile từ đó → không thể lệch.

`test/check_fingerprint.py` bắt request thật qua socket local, so bảng
`fingerprint.PROFILES` với header `curl_cffi` gửi — lệch là fail.

---

## 2. Vân tay nào qua được Cloudflare

**Cloudflare đổi danh sách JA3 được phép theo thời gian.** Chạy lại
`test/probe_fingerprints.py` rồi xếp lại `PROFILES` — đừng tin bảng cũ.

Đo **2026-07-27** (proxy VN), `test/probe_fingerprints.py`:

| Profile | `/auth/login` | `/api/auth/csrf` |
|---------|---------------|------------------|
| **safari18_0** | 200 | 200 |
| **chrome124** | 200 | 200 |
| chrome136 | 403 | 403 |
| chrome131 | 403 | 403 |
| chrome120 | 403 | 200 |
| chrome145 | 403 | 403 |

So với lần đo trước cùng ngày, allow-list đã đổi: `chrome124` qua cả hai cửa và
`chrome136` bị 403. Vì thế `PROFILES` xếp safari18_0 + chrome124 lên đầu,
chrome145 vẫn cuối. Lưu ý cột `/api/auth/csrf` (backend chatgpt.com) khoan dung
hơn cửa trước với chrome120, nhưng vẫn nên dùng chung profile đã bootstrap cho
toàn bộ auth flow để không tạo fingerprint mâu thuẫn.

Bản `curl_cffi` **mới nhất bị chặn nhiều nhất** — đừng mặc định "mới hơn = tốt hơn".

Repo tham chiếu (github.com/Regert888/gpt-outlook-register) dùng `safari18_0` làm
mặc định, khớp với kết quả đo.

`PROFILES` trong `gpt_reg/fingerprint.py` xếp theo thứ tự này; `_bootstrap_with_profile_rotation`
xoay **cả bộ** khi gặp CF 403 (`step="cf_block"`).

### Khi HTTP reg fail hàng loạt vì CF 403

```powershell
.venv311\Scripts\python test\probe_fingerprints.py      # profile nào còn 200
.venv311\Scripts\python test\probe_default_headers.py   # lấy UA/sec-ch-ua thật
```

Sắp lại `PROFILES` theo kết quả, cập nhật `user_agent` / `sec_ch_ua` bằng đúng
giá trị probe in ra, rồi chạy `test\check_fingerprint.py`.

---

## 3. Mỗi lần chạy có trùng lần trước không?

**Không trùng ở phần định danh, nhưng CÓ trùng ở phần vân tay — và điều đó là đúng.**

| Thành phần | Đổi mỗi lần? | Ghi chú |
|---|---|---|
| `device_id` (`oai-did`) | **Có** — `uuid.uuid4()` | định danh thiết bị |
| IP thoát | **Có** — proxy xoay | xem mục 4 |
| Cookie jar | **Có** — session mới hoàn toàn | |
| Datadog `traceparent` | **Có** — `random.getrandbits(64)` mỗi request | |
| Sentinel token | **Có** — bind device_id + challenge server | |
| Camoufox profile dir | **Có** — `camoufox_<uuid8>` | |
| TLS fingerprint | **Không** (trong cùng profile) | **cố ý** |
| User-Agent / client hints | **Không** (trong cùng profile) | **cố ý** |

**Vì sao vân tay KHÔNG nên ngẫu nhiên:** hàng triệu người dùng Chrome 124 trên
macOS có TLS fingerprint **giống hệt nhau** — đó là chỗ để lẫn vào đám đông. Vân
tay ngẫu nhiên tạo ra tổ hợp không tồn tại trong thực tế và **dễ bị bắt hơn**.
Chống bot muốn thấy bạn phổ biến, không muốn thấy bạn độc nhất.

Muốn đa dạng thì đổi **giữa các profile có thật** (`PROFILES`), không phải chế ra
giá trị mới. Hiện `_bootstrap_with_profile_rotation` bắt đầu từ `PROFILES[0]` và
chỉ xoay khi bị chặn — muốn rải đều thì cho mỗi job chọn ngẫu nhiên một profile
trong nhóm đã đo là "qua được".

---

## 4. IP — vấn đề chưa giải quyết

Proxy hiện tại: `us.arxlabs.io:3010`, residential Việt Nam, **xoay IP theo từng
kết nối TCP**.

Đo được (`test/probe_fingerprints.py` cùng logic):

| Kịch bản | Kết quả |
|---|---|
| 5 lần gọi, mỗi lần session mới | **5 IP khác nhau** |
| 6 request cùng session, **cùng host** | **1 IP** (keep-alive giữ kết nối) |
| 4 request cùng session, **khác host** | **3 IP khác nhau** |

### Vì sao đây là vấn đề

Luồng đăng ký đi qua **3 host**:

```
chatgpt.com          → csrf, signin/openai      → IP A
auth.openai.com      → authorize, register, OTP → IP B
sentinel.openai.com  → sinh sentinel token      → IP C
```

Sentinel token sinh từ IP C nhưng gửi kèm request tới IP B. State machine OAuth
trải trên chatgpt.com → auth.openai.com với hai IP khác nhau.

**Triệu chứng quan sát được:** `409 invalid_state` ("Your sign-in session is no
longer valid") xảy ra **3/4 lần** trong lúc dò. Chưa chứng minh nhân quả, nhưng
đây là nghi phạm số một.

### Đã thử và KHÔNG được

Nhà cung cấp không nhận các định dạng sticky session thông dụng — cả 4 đều xoay IP:

```
zhanghao001-region-VN-sessid-XXX:pw@us.arxlabs.io:3010
zhanghao001-region-VN-session-XXX:pw@...
zhanghao001-region-VN-sess-XXX:pw@...
zhanghao001-region-VN-st-XXX:pw@...
```

### Cách xử lý cho agent sau

1. **Hỏi nhà cung cấp cú pháp sticky session.** `gpt_reg/proxy/format.py` đã hỗ
   trợ placeholder `{sid}` — mỗi lần `materialize_proxy()` thay bằng chuỗi ngẫu
   nhiên 12 ký tự. Chỉ cần biết cú pháp đúng rồi đặt vào `proxy.pool`:
   ```
   zhanghao001-region-VN-<CÚ_PHÁP_ĐÚNG>-{sid}:abc123@us.arxlabs.io:3010
   ```
   Mỗi job sẽ có IP cố định riêng — đúng thứ cần.
2. Hoặc đổi sang proxy có sticky session (thường gọi là "sticky/session port").
3. Đo lại bằng đoạn script trong mục này: 1 session, nhiều host, xem có ra 1 IP không.

**Browser mode ít nhạy hơn** (5/5 thành công) vì Camoufox giữ connection pool lâu
hơn và ít host hơn.

---

## 5. Sentinel — proof-of-work

`sentinel.openai.com` phát challenge; token phải sinh từ **sdk.js thật của OpenAI**.

| Đường | Cách chạy | Rủi ro |
|---|---|---|
| **QuickJS** (chính) | Node chạy sdk.js thật | cần Node 18+ |
| **Python PoW** (dự phòng) | FNV-1a thuần Python | qua được `/sentinel/req` nhưng **server có thể silent-drop OTP** (200 OK mà không gửi mail) |

Đo: QuickJS sinh token trong **3.0–3.8s** (`p≈330-350`, `c≈1900-2200`).

`_get_sentinel_token` thử QuickJS **3 lần** trước khi rơi về PoW — vì một lỗi
mạng chớp nhoáng (`curl: (35) TLS connect error`, đã gặp thật) không đáng đánh đổi
bằng nguy cơ mất OTP. Log cảnh báo rõ khi phải dùng PoW.

`navigator_payload` phải khớp profile của session, nếu không sdk.js thấy
`navigator.userAgent` khác UA trên dây → mâu thuẫn.

Biến môi trường:
- `OPENAI_SENTINEL_NODE_PATH` — chỉ đường node binary
- `OPENAI_SENTINEL_DISABLE_QUICKJS=1` — tắt QuickJS (chỉ để test PoW)

**Bẫy đã sửa:** `SentinelNodeWorker.run_action` dựa vào `readline()` để hết giờ.
Nếu Node còn sống mà kẹt vòng lặp đồng bộ (sdk.js chạy dưới `eval`, `setTimeout`
bị override thành đồng bộ) thì `readline()` block vĩnh viễn, giữ luôn `self._lock`
→ treo cả job, không rơi được về PoW. Nay đọc trong thread phụ có deadline, hết
giờ thì giết process. Test: `test/check_sentinel_worker.py`.

---

## 6. Luồng HTTP đăng ký — chuỗi request ĐÚNG

Xác định bằng cách **bắt request thật của browser**
(`test/probe_browser_capture.py`) rồi thử từng biến thể
(`test/probe_register_variants.py`).

```
GET  chatgpt.com/auth/login              ← prime cookie Cloudflare (__cf_bm)
GET  chatgpt.com/api/auth/csrf           ← csrfToken
POST chatgpt.com/api/auth/signin/openai  ← → authorize URL
GET  auth.openai.com/api/accounts/authorize   ← device_id (oai-did)
GET  auth.openai.com/create-account/password  ← HEADER HTML, không phải JSON
POST auth.openai.com/api/accounts/user/register    ← KHÔNG gửi sentinel
GET  auth.openai.com/api/accounts/email-otp/send
     ↓ poll OTP qua Graph
POST auth.openai.com/api/accounts/email-otp/validate
POST auth.openai.com/api/accounts/create_account   ← CÓ gửi sentinel
     ↓ follow redirects → callback
GET  chatgpt.com/api/auth/session        ← access_token
```

### Ba điều PHẢN TRỰC GIÁC, đều đo được

**1. KHÔNG gọi `authorize/continue`.** Tôi từng thêm bước này vì tưởng nó bắt buộc.
Sai. Gọi nó làm server chuyển sang nhánh `email_otp_verification` (passwordless),
và `user/register` sau đó bị **400 `invalid_auth_step`**.

**2. Nút "Continue with password" KHÔNG gọi API nào.** Bắt request từ browser xác
nhận: chỉ đổi route SPA phía client sang `/create-account/password`. Không có
endpoint nào để "chuyển sang chế độ mật khẩu".

**3. Request `user/register` KHÔNG gửi `openai-sentinel-token`.** Browser không
gửi. Gửi vào thì bị từ chối. (Nhưng `create_account` thì CÓ gửi.)

Bảng đo 4 biến thể trên cùng một account:

| Biến thể | Kết quả |
|---|---|
| **no-continue + header HTML + no-sentinel** | **HTTP 200** |
| no-continue + header HTML + sentinel | 400 invalid_auth_step |
| no-continue + header JSON + sentinel | 400 invalid_auth_step |
| có-continue + header HTML + no-sentinel | 400 invalid_auth_step |

### Đọc mã lỗi

| Mã | Nghĩa thật |
|---|---|
| `invalid_auth_step` | State machine sai bước **hoặc** email đã đăng ký. Nếu chuỗi request đã đúng thì gần như chắc là đã đăng ký. |
| `invalid_state` | Phiên OAuth hỏng — bootstrap lại từ đầu (session mới, device_id mới). Nghi do IP xoay. |
| `string_below_min_length` (password) | Mật khẩu < 12 ký tự. `build_request_from_combo` tự pad, đừng parse combo thô. |
| `rate_limit_exceeded` | Dò quá nhiều. Nghỉ ~90s. |

---

## 7. Kết quả live đã đo

### Browser mode (5/5 thành công)

| Email | Wall | browser | mfa |
|---|---|---|---|
| SegobiaAlvarez3459 | 121.9s | 92.0s | 26.6s |
| DarlaDugger366 | 299.8s | 278.8s | 21.0s |
| DidatoBascetta11 | 74.5s | 59.9s | 13.7s |
| HenniganSharpless849 | 85.6s | 63.0s | 21.6s |

### HTTP mode

Batch live ngày 2026-07-27: **8/8 thành công HTTP thuần**, fallback tắt, mỗi job
27,0–40,7s, `browser_seconds` rỗng và session có access token. Landing OTP phải
gọi `/email-otp/resend` để nhận **login code**; dùng `/email-otp/send` tạo
verification code và đẩy sai sang onboarding `/about-you`.

HTTP không mở Camoufox và nhanh hơn đáng kể trên batch này.

---

## 8. Script đo — chạy tay, không nằm trong `setup.bat`

| Script | Dùng khi |
|---|---|
| `test/probe_fingerprints.py` | HTTP reg fail hàng loạt vì CF 403 |
| `test/probe_default_headers.py` | Nâng cấp `curl_cffi`, cần lấy lại UA/sec-ch-ua thật |
| `test/probe_http_register.py` | Xem nguyên văn response bước register |
| `test/probe_register_variants.py` | OpenAI đổi luồng, cần dò lại biến thể nào qua |
| `test/probe_browser_capture.py` | Cần biết browser thật gửi request gì (nguồn sự thật) |
| `test/probe_password_switch.py` | Dò tham số `authorize/continue` |
| `test/probe_browser_otp_capture.py` | Bắt endpoint Resend/Validate của passwordless OTP |
| `test/probe_login_with.py` | Dò bundle công khai của route `/auth/login_with` |

**Khi OpenAI đổi luồng, dùng `probe_browser_capture.py` trước** — browser mode
vẫn chạy được, nên nó là nguồn sự thật để đối chiếu.

Test tự động (có trong `setup.bat`): `check_fingerprint`, `check_http_reg`,
`check_sentinel_worker`, `check_concurrency`, `check_smsbower`, `check_export`,
`check_ui_wiring`, `check_sse`, `check_job_api`, `check_otp_retry`,
`check_browser_screens`, `check_about_you_fields`.

---

## 9. Việc còn lại

1. **Sticky-session proxy** (mục 4) — nghi là nguyên nhân `invalid_state`. Ưu tiên cao nhất.
2. **Rải profile ngẫu nhiên** trong nhóm "qua được" thay vì luôn bắt đầu `PROFILES[0]`.
3. **Xoay `PROFILES` theo lịch** — CF đổi luật, cần đo lại định kỳ.
4. **Luồng tạo Gmail bằng số thuê** — mới có hạ tầng SMS (số dư + tồn kho), chưa có automation.
5. **Đo lại `invalid_state`** sau khi có sticky proxy, xem tỷ lệ có giảm không.
