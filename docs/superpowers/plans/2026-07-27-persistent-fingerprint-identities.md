# Persistent Fingerprint Identities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cấp cho mỗi job một fingerprint identity cố định, dùng HTTP preset đã kiểm chứng và Camoufox preset thực, đồng thời giữ nguyên identity qua retry/fallback.

**Architecture:** `gpt_reg/fingerprint.py` là registry và bộ dẫn xuất identity HTTP từ seed. SQLite sở hữu seed/profile/config Browser; `gpt_reg/browser/fingerprint.py` materialize một Camoufox preset thực rồi repository lưu cấu hình đầy đủ. HTTP, Sentinel, phase lấy token và MFA dùng cùng profile; Browser đọc lại config đã lưu thay vì sinh mới.

**Tech Stack:** Python 3.11, SQLite, curl_cffi 0.15, Camoufox 0.5, Pydantic 2, các script `test/check_*.py` và `test/smoke_*.py`.

> Repository hiện không có `.git`, vì vậy các bước commit được thay bằng checkpoint kiểm thử và ghi trạng thái ngay trong plan.

---

### Task 1: Identity core và registry HTTP canonical

**Files:**
- Modify: `gpt_reg/fingerprint.py`
- Create: `test/check_fingerprint_identity.py`
- Modify: `test/check_fingerprint.py`

- [x] **Step 1: Viết test đỏ cho identity xác định và fail-fast**

`test/check_fingerprint_identity.py` kiểm tra API mong muốn:

```python
from gpt_reg.fingerprint import (
    PROFILES, candidate_profiles, device_id_for_seed, get_profile,
    identity_id, profile_for_seed,
)

def main() -> int:
    failures = []
    seeds = [f"{i:032x}" for i in range(200)]
    ids = [identity_id(seed) for seed in seeds]
    devices = [device_id_for_seed(seed) for seed in seeds]
    if len(set(ids)) != 200 or len(set(devices)) != 200:
        failures.append("200 seed không tạo 200 identity/device ID")
    for seed in seeds:
        if profile_for_seed(seed) != profile_for_seed(seed):
            failures.append("profile không xác định theo seed")
        order = candidate_profiles(seed)
        if order[0] != profile_for_seed(seed) or set(order) != set(PROFILES):
            failures.append("candidate order sai")
    try:
        get_profile("profile-khong-ton-tai")
        failures.append("profile lạ không fail-fast")
    except ValueError:
        pass
    print("[fail] fingerprint identity" if failures else "[ok] fingerprint identity")
    return len(failures)
```

- [x] **Step 2: Chạy test và xác nhận RED**

Run: `.venv311\Scripts\python.exe test\check_fingerprint_identity.py`

Expected: lỗi import các helper identity chưa tồn tại hoặc assertion profile lạ thất bại.

- [x] **Step 3: Thay registry cũ bằng 24 target canonical đã probe**

Giữ `Profile` tương thích với caller hiện tại nhưng bổ sung `navigator_platform`, `vendor`,
`has_user_agent_data`, `hardware_options`. Registry phải có đúng các target:

```python
CANONICAL_TARGETS = (
    "edge99", "edge101", "chrome99", "chrome100", "chrome101", "chrome104",
    "chrome107", "chrome110", "chrome116", "chrome124", "chrome131", "chrome142",
    "chrome99_android", "safari153", "safari180", "safari180_ios", "safari184",
    "safari184_ios", "safari2601", "firefox133", "firefox135", "firefox144",
    "firefox147", "tor145",
)
```

UA, `sec-ch-ua`, platform và Accept-Language được chép đúng từ request local của
`curl_cffi`; không tạo alias. Chromium/Edge/Android có `has_user_agent_data=True`;
Safari/Firefox/Tor là `False`. `get_profile(None)` trả default chỉ khi caller không chỉ định;
tên không rỗng nhưng không tồn tại ném `ValueError`.

- [x] **Step 4: Thêm helper dẫn xuất identity**

```python
_DEVICE_NAMESPACE = uuid.UUID("365ed1a8-24b0-5e18-9e4e-87bb2f6a1189")

def validate_seed(seed: str) -> str:
    value = str(seed).strip().lower()
    if len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("fingerprint_seed must be 128-bit lowercase hex")
    return value

def new_seed() -> str:
    return secrets.token_hex(16)

def identity_id(seed: str) -> str:
    return hashlib.sha256(("identity:" + validate_seed(seed)).encode()).hexdigest()[:12]

def device_id_for_seed(seed: str, purpose: str = "http") -> str:
    return str(uuid.uuid5(_DEVICE_NAMESPACE, f"{validate_seed(seed)}:{purpose}"))

def profile_for_seed(seed: str) -> Profile:
    value = validate_seed(seed)
    return max(PROFILES, key=lambda p: hashlib.sha256(f"{value}:{p.name}".encode()).digest())

def candidate_profiles(seed: str, preferred: str | None = None) -> tuple[Profile, ...]:
    first = get_profile(preferred) if preferred else profile_for_seed(seed)
    tail = sorted((p for p in PROFILES if p != first),
                  key=lambda p: hashlib.sha256(f"{seed}:{p.name}:fallback".encode()).digest(),
                  reverse=True)
    return (first, *tail)
```

`navigator_payload(profile, seed)` chọn một tuple CPU/RAM thuộc `hardware_options` bằng
hash seed; không phát sinh ngẫu nhiên. Payload có vendor/platform đúng engine và cờ
`has_user_agent_data` để JS không dựng Client Hints cho Safari/Firefox.

- [x] **Step 5: Chạy GREEN cho identity và header capture**

Run:

```powershell
.venv311\Scripts\python.exe test\check_fingerprint_identity.py
.venv311\Scripts\python.exe test\check_fingerprint.py
```

Expected: cả hai in `[ok]`; test socket local xác nhận đủ 24 profile khớp header thật.

### Task 2: Lưu identity nguyên tử trong SQLite

**Files:**
- Modify: `gpt_reg/db/schema.py`
- Modify: `gpt_reg/db/repositories.py`
- Create: `test/check_fingerprint_storage.py`

- [x] **Step 1: Viết test đỏ migration và retry ổn định**

Test tạo SQLite tạm, migrate, chèn một job cũ không có fingerprint rồi gọi
`ensure_fingerprint_identity()` hai lần. Assertion bắt buộc:

```python
first = repo.ensure_fingerprint_identity("legacy-job")
second = repo.ensure_fingerprint_identity("legacy-job")
assert first == second
assert len(first["fingerprint_seed"]) == 32
assert get_profile(first["fingerprint_profile"])
stored = repo.set_fingerprint_data_if_empty("legacy-job", '{"version":1}')
assert repo.set_fingerprint_data_if_empty("legacy-job", '{"version":2}') == stored
```

Test đồng thời xác nhận `migrate()` trả version 5 và các cột mới tồn tại.

- [x] **Step 2: Chạy và xác nhận RED**

Run: `.venv311\Scripts\python.exe test\check_fingerprint_storage.py`

Expected: schema vẫn là v4 hoặc repository thiếu method.

- [x] **Step 3: Thêm schema v5**

Trong `schema.py`, đặt `CURRENT_VERSION = 5` và thêm vào `ADD_COLUMNS`:

```python
("jobs", "fingerprint_seed", "TEXT"),
("jobs", "fingerprint_profile", "TEXT"),
("jobs", "fingerprint_data", "TEXT"),
```

- [x] **Step 4: Thêm repository API dưới cùng write lock**

`ensure_fingerprint_identity(job_id)` đọc/ghi trong một critical section. Job cũ dùng
`sha256("legacy-job:" + job_id)[:32]`; job mới đã có seed thì validate. Profile trống được
chọn bằng `profile_for_seed`; profile lạ gọi `get_profile` và ném lỗi. Nếu chỉ seed hoặc chỉ
profile tồn tại thì coi dữ liệu hỏng và fail-fast, không tự ghép nửa identity. Method trả
dict ba trường nhưng không log seed.

`set_fingerprint_data_if_empty(job_id, payload)` dùng `COALESCE(fingerprint_data, ?)` và
trả payload đang lưu, nhờ đó caller thứ hai không thể ghi đè cấu hình đầu tiên.

- [x] **Step 5: Chạy GREEN và regression DB**

Run:

```powershell
.venv311\Scripts\python.exe test\check_fingerprint_storage.py
.venv311\Scripts\python.exe test\check_job_api.py
```

Expected: `[ok]`; API allowlist hiện tại không lộ seed/config.

### Task 3: Materialize Camoufox preset thực và validate dữ liệu lưu

**Files:**
- Create: `gpt_reg/browser/fingerprint.py`
- Create: `test/check_browser_fingerprint_identity.py`
- Modify: `gpt_reg/phases/browser/__init__.py`
- Modify: `gpt_reg/models.py`

- [x] **Step 1: Viết test đỏ cho preset thực, noise cố định và dữ liệu lỗi**

Test gọi `materialize_browser_fingerprint(seed)` rồi kiểm tra:

```python
one = materialize_browser_fingerprint("01" * 16)
two = materialize_browser_fingerprint("01" * 16)
assert one["preset_id"] == two["preset_id"]
for key in ("fonts:spacing_seed", "audio:seed", "canvas:seed"):
    assert one["config"][key] == two["config"][key]
for forbidden in (
    "timezone", "locale:language", "navigator.language", "navigator.languages",
    "headers.Accept-Language", "geolocation:latitude", "webrtc:ipv4",
):
    assert forbidden not in one["config"]
assert parse_browser_fingerprint(json.dumps(one)) == one
```

JSON sai version, thiếu preset ID/config hoặc chứa khóa geo/WebRTC phải ném
`BrowserFingerprintError`, không trả BrowserForge config.

- [x] **Step 2: Chạy và xác nhận RED**

Run: `.venv311\Scripts\python.exe test\check_browser_fingerprint_identity.py`

Expected: module chưa tồn tại.

- [x] **Step 3: Cài bộ chọn preset xác định**

Module mới gọi `camoufox.fingerprints.load_presets()` với major Firefox đã cài, flatten
`presets.macos/windows/linux`, canonicalize từng preset bằng JSON sort-key và tạo
`preset_id = sha256(canonical)[:16]`. Chọn candidate bằng rendezvous hash từ seed. Payload
lưu `schema`, commitment SHA-256 của seed, preset đã sanitize, `preset_id`, `bundle_sha256`, `camoufox_version`,
`firefox_major` và CAMOU_CONFIG hoàn chỉnh.

Khởi tạo `config` với ba seed 32-bit và `window.history.length` dẫn xuất từ SHA-256, sau đó
gọi `camoufox.utils.launch_options(config=config, fingerprint_preset=preset, headless=False)`
để Camoufox bổ sung navigator/screen/WebGL/fonts/voices. Loại addons và toàn bộ key bắt đầu
bằng `timezone`, `locale:`, `navigator.language`, `headers.Accept-Language`, `geolocation:`
hoặc `webrtc:`. Parser fail-fast khi schema, seed commitment, bundle hash, preset hoặc Firefox major không
hợp lệ; profile đã lưu tự chứa preset/config nên không chọn lại candidate.

- [x] **Step 4: Truyền config vào Browser phase**

Thêm vào `SignupRequest`:

```python
fingerprint_seed: str = Field(default_factory=new_seed, repr=False)
fingerprint_profile: str | None = None
browser_fingerprint: dict[str, Any] | None = Field(default=None, repr=False)
```

Browser phase parse/validate dict trước khi mở process. Nếu chưa có dữ liệu (CLI trực tiếp),
materialize một lần từ seed; nếu có dữ liệu hỏng thì fail-fast. Luôn `deepcopy` preset/config
vì Camoufox mutate dict khi thêm geoip/WebRTC. Khởi chạy:

```python
cf = AsyncCamoufox(
    config=deepcopy(browser_fp["config"]),
    fingerprint_preset=deepcopy(browser_fp["preset"]),
    i_know_what_im_doing=True,
    headless=request.headless,
    persistent_context=True,
    user_data_dir=str(profile_dir),
    locale=ctx.settings.browser_locale,
    geoip=bool(proxy_mat) and ctx.settings.browser_geoip,
    proxy=proxy_kw or None,
)
```

Không truyền `fingerprint_preset=None/False` theo nhánh fallback; preset và config đã lưu là
nguồn duy nhất. Device ID Browser bootstrap dùng `device_id_for_seed(seed, "browser")` thay
cho `uuid4()`.

- [x] **Step 5: Chạy GREEN**

Run: `.venv311\Scripts\python.exe test\check_browser_fingerprint_identity.py`

Expected: `[ok] browser fingerprint identity`.

### Task 4: Giữ một HTTP profile qua bootstrap, Sentinel và phase lấy token

**Files:**
- Modify: `gpt_reg/phases/http_reg.py`
- Modify: `gpt_reg/phases/http.py`
- Modify: `gpt_reg/sentinel/quickjs.py`
- Modify: `gpt_reg/sentinel/pow.py`
- Modify: `gpt_reg/sentinel/openai_sentinel_quickjs.js`
- Modify: `gpt_reg/phases/mfa.py`
- Modify: `gpt_reg/signup.py`
- Modify: `gpt_reg/models.py`
- Modify: `test/check_http_reg.py`
- Modify: `test/check_fingerprint.py`

- [x] **Step 1: Viết test đỏ cho candidate order và navigator theo engine**

Test mock `_step_csrf` để profile đầu trả `cf_block`, profile thứ hai thành công; xác nhận chỉ
xoay trước auth state và session trả về gắn profile thứ hai. Test payload Safari/Firefox
không có User-Agent Client Hints, vendor/platform không phải Google/Win32. Test
`_fetch_access_token` không tự set `User-Agent`, `sec-ch-ua`, `Accept-Language`.

- [x] **Step 2: Chạy các test liên quan và xác nhận RED**

Run:

```powershell
.venv311\Scripts\python.exe test\check_http_reg.py
.venv311\Scripts\python.exe test\check_fingerprint.py
```

Expected: signature bootstrap chưa nhận seed/profile hoặc header phase 2 còn ghi đè.

- [x] **Step 3: Sửa HTTP bootstrap**

`_bootstrap_with_profile_rotation()` nhận `fingerprint_seed` và `preferred_profile`, dùng
`candidate_profiles()`, đồng thời dùng `device_id_for_seed()` thay UUID ngẫu nhiên. Nó chỉ
continue với `_is_tls_error`/`_is_cf_block`; các lỗi khác raise ngay.

`_run_sync()` thêm profile thật của session cuối vào result; `HttpRegPhase.run()` ghi
`user_agent`, `impersonate`, `fingerprint_profile` vào `BrowserHandoff`. Re-bootstrap do
`invalid_state` dùng lại seed, candidate order và device ID cũ.

- [x] **Step 4: Sửa phase lấy token/MFA dùng profile thật**

Mở rộng `BrowserHandoff` với ba field tùy chọn trên. Trong `run_signup`, tạo
`network_request = req.model_copy(update={...})` từ handoff nếu HTTP đã rotate, rồi truyền
request đó cho `run_http_phase()` và `enable_2fa()`.

Trong `gpt_reg/phases/http.py`, header contextual chỉ còn:

```python
headers = {"Accept": "application/json", "Referer": "https://chatgpt.com/"}
```

Không set UA, Client Hints hoặc Accept-Language thủ công.

`enable_2fa()` nhận profile name thay vì cặp UA/impersonate rời, resolve bằng registry rồi
để `curl_cffi` tự gửi identity headers. Session file ghi UA/profile thực tế của
`network_request`, không ghi default cũ.

- [x] **Step 5: Làm Sentinel navigator đúng engine**

Python luôn gọi `navigator_payload(profile, fingerprint_seed)`. JS dựng `navigator` từ
`platform`/`vendor`; chỉ gắn `deviceMemory` khi payload có giá trị và chỉ gắn
`userAgentData` khi `has_user_agent_data=True`. Không suy Chrome brands từ UA Firefox/Safari.
Python PoW dùng cùng payload deterministic, không random `hardwareConcurrency` lại theo token.
QuickJS/Sentinel lỗi phải báo đúng lỗi; không âm thầm đổi sang navigator/PoW persona khác.

- [x] **Step 6: Chạy GREEN**

Run:

```powershell
.venv311\Scripts\python.exe test\check_http_reg.py
.venv311\Scripts\python.exe test\check_fingerprint.py
.venv311\Scripts\python.exe test\check_session_merge.py
```

Expected: tất cả `[ok]`.

### Task 5: Cấp và tái sử dụng identity trong job manager

**Files:**
- Modify: `gpt_reg/web/jobs/reg_manager.py`
- Modify: `test/check_job_api.py`
- Modify: `test/check_http_fallback.py`
- Modify: `test/check_fingerprint_storage.py`

- [x] **Step 1: Viết test đỏ cho batch 200 và retry/fallback**

Test tạo 200 job nhưng không khởi chạy mạng, đọc repository và xác nhận 200 seed/identity ID/
device ID duy nhất. Retry một job giữ nguyên seed/profile/data. Mock hai engine trong test
fallback và xác nhận cả hai `SignupRequest` nhận cùng seed/config.

- [x] **Step 2: Chạy và xác nhận RED**

Run:

```powershell
.venv311\Scripts\python.exe test\check_fingerprint_storage.py
.venv311\Scripts\python.exe test\check_http_fallback.py
```

Expected: job chưa có ba trường identity hoặc request chưa nhận chúng.

- [x] **Step 3: Cấp identity cho job mới và lazy-backfill job cũ**

Khi tạo job, sinh seed bằng `new_seed()`, chọn profile bằng `profile_for_seed()` rồi đưa hai
field vào `jobs_repo.create`. Khi retry và trước `_attempt_signup`, gọi
`ensure_fingerprint_identity()` để backfill/validate.

Chỉ khi attempt hiện tại thực sự có `reg_mode == "browser"` và `fingerprint_data` trống,
materialize rồi gọi
`set_fingerprint_data_if_empty`; parse lại payload repository trả về để chống race. Tạo
`SignupRequest` với seed/profile/config và UA/impersonate lấy từ `get_profile(profile)`.
Retry không bao giờ clear `fingerprint_seed`, `fingerprint_profile` hoặc `fingerprint_data`.

- [x] **Step 4: Thêm log không lộ seed**

Đầu mỗi attempt ghi:

```python
log(f"[fingerprint] identity={identity_id(seed)} engine={reg_mode} profile={profile.name}")
```

Không đưa `fingerprint_seed` hoặc `fingerprint_data` vào `_job_for_api`, SSE hay export.

- [x] **Step 5: Chạy GREEN**

Run:

```powershell
.venv311\Scripts\python.exe test\check_fingerprint_storage.py
.venv311\Scripts\python.exe test\check_http_fallback.py
.venv311\Scripts\python.exe test\check_job_api.py
```

Expected: tất cả `[ok]`.

### Task 6: Khóa dependency, smoke Browser và regression đầy đủ

**Files:**
- Modify: `pyproject.toml`
- Modify: `setup.bat`
- Create: `test/smoke_browser_fingerprint.py`
- Modify khi test chỉ ra regression: các file thuộc Task 1-5

- [x] **Step 1: Khóa phiên bản transport đã probe**

Khóa `curl_cffi==0.15.0`, `camoufox[geoip]==0.5.4` và runtime
`official/stable/152.0.4-beta.28` để TLS/header table, preset bundle và Firefox binary không
âm thầm đổi dưới chân ứng dụng.

- [x] **Step 2: Viết smoke Browser hai lần launch**

Smoke materialize một payload, mở hai Camoufox context tuần tự bằng cùng config và đọc:

```javascript
({
  ua: navigator.userAgent,
  platform: navigator.platform,
  cores: navigator.hardwareConcurrency,
  screen: [screen.width, screen.height, screen.availWidth, screen.availHeight],
  webgl: (() => {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl');
    const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
    return ext ? [gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),
                  gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)] : null;
  })(),
})
```

Assertion hai snapshot bằng nhau và UA là Firefox; smoke chỉ dùng trang `about:blank`.

- [x] **Step 3: Chạy backend regression**

Run: `.venv311\Scripts\python.exe test\run_all.py`

Expected: mọi `check_*.py` pass, không warning/error mới.

- [x] **Step 4: Chạy smoke Browser**

Run: `.venv311\Scripts\python.exe test\smoke_browser_fingerprint.py`

Expected: `[ok] browser fingerprint stable` và không còn process Camoufox/Playwright sau khi thoát.

- [x] **Step 5: Chạy probe HTTP local cuối**

Run:

```powershell
.venv311\Scripts\python.exe test\check_fingerprint.py
.venv311\Scripts\python.exe test\probe_default_headers.py
```

Expected: 24 profile capture được; bảng runtime khớp UA/Client Hints/platform. Không chạy tài
khoản thật trong bước này và không ghi credential vào artifact.
