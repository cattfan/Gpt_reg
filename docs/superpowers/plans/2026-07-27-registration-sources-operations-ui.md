# Registration Sources And Operations UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hoan thien ba nguon dang ky Hotmail/Outlook, Gmail (SMSBower), Gmail (AccStack), cung danh tinh ho so, proxy random co chon loc, log ben vung va UI van hanh on dinh tren desktop/mobile.

**Architecture:** FastAPI va cac job manager chi dieu phoi; moi nha cung cap mail thue duoc boc trong adapter cung contract va state rental duoc luu SQLite. Vue doc cac API co kieu ro rang, hien control theo source, log theo row duoc chon, va settings tach Integrations/Proxy; secret chi ton tai trong SQLite va response luon masked.

**Tech Stack:** Python 3.11, FastAPI, SQLite WAL, httpx/curl_cffi, Pydantic, Vue 3, TypeScript, vue-i18n, Lucide, Tailwind CSS 4/CSS tokens, Vitest, Playwright CLI.

---

## File Map

- `gpt_reg/db/schema.py`: schema v7 cho rental, proxy, check log va metadata job.
- `gpt_reg/db/repositories.py`: repository settings, proxy, rental, job va check log.
- `gpt_reg/profile_identity.py`: generator ten/ngay sinh co seed cho `vi`, `ko`, `in`.
- `gpt_reg/proxy/pool.py`: random resolver theo cong tac va selected subset.
- `gpt_reg/mail/rental.py`: contract, model va loi chung cua mail rental.
- `gpt_reg/mail/smsbower_rental.py`: adapter temporary Gmail cua SMSBower.
- `gpt_reg/mail/accstack.py`: adapter Gmail rental cua AccStack.
- `gpt_reg/mail/alias.py`: tao Gmail plus alias on dinh, khong trung.
- `gpt_reg/web/jobs/rental_coordinator.py`: vong doi mailbox -> alias -> job con.
- `gpt_reg/web/jobs/reg_manager.py`: tao job theo source, tai su dung profile/proxy.
- `gpt_reg/web/jobs/check_manager.py`: persist check log da sanitize.
- `gpt_reg/phases/http_reg.py`: trace 10 checkpoint lien tuc va ket qua account-exists co cau truc.
- `gpt_reg/web/server.py`: mail status, proxy CRUD, start payload va check log API.
- `frontend/src/{types.ts,services/api.ts,i18n.ts}`: contract UI va ba ban dich.
- `frontend/src/views/RegistrationView.vue`: source, quantity, profile, Jobs va copy log.
- `frontend/src/views/CheckAccountsView.vue`: selected row, historical/realtime log va copy.
- `frontend/src/views/SettingsView.vue`: Integrations/Proxy, khong con Appearance.
- `frontend/src/styles.css`: flex-fill Jobs, settings responsive va control on dinh.
- `test/check_*.py`: regression offline; `test/smoke_rental_mail.py`: smoke co chi phi goi rieng.

### Task 1: Git Hygiene Va Baseline

**Files:**
- Modify: `.gitignore`
- Create: `docs/superpowers/plans/2026-07-27-registration-sources-operations-ui.md`

- [ ] **Step 1: Chan artifact runtime va local tool**

Dat cac rule sau, van track `runtime/.gitkeep`:

```gitignore
.claude/
.playwright-cli/
output/
runtime/*
!runtime/.gitkeep
```

- [ ] **Step 2: Quet secret va du lieu account truoc stage**

Run:

```powershell
git status --short
rg -l --hidden --glob '!.git/**' --glob '!runtime/**' --glob '!.playwright-cli/**' 'X-API-Key:|M\.C[0-9]+_|hotmail\.com\|' .
```

Expected: khong co source/frontend/doc chua key that, refresh token hoac combo that.

- [ ] **Step 3: Tao baseline commit va push main**

```powershell
git add .
git commit -m "chore: establish gpt-reg baseline"
git push -u origin main
```

Expected: remote `origin/main` co commit baseline, artifact local khong nam trong index.

### Task 2: SQLite Schema Va Repository Contracts

**Files:**
- Modify: `gpt_reg/db/schema.py`
- Modify: `gpt_reg/db/repositories.py`
- Create: `test/check_registration_storage.py`

- [ ] **Step 1: Viet check do cho migration v7**

Tao DB in-memory, goi `migrate()`, assert `mail_rentals`, `check_logs`, `proxies` ton tai; assert `jobs` co `rental_id`, `source_email`, `alias_index`, `profile_region`, `profile_name`, `birthdate`; assert `proxy.rotation_mode` bi xoa.

```python
assert migrate(conn) == 7
assert {"mail_rentals", "check_logs", "proxies"} <= table_names(conn)
assert repo.get("proxy.rotation_mode") raises KeyError
```

- [ ] **Step 2: Chay check va xac nhan RED**

Run: `python test/check_registration_storage.py`

Expected: FAIL vi schema version van la 6 va thieu bang/cot.

- [ ] **Step 3: Them schema va repository**

Them `MailRentalRepository` voi `create/get/update/list_active`, `ProxyRepository` voi `list_all/replace_all`, va `ChecksRepository.append_log/logs/clear_logs`. Moi write dung lock va transaction; `replace_all()` validate toan bo truoc khi ghi. Them settings allowlist:

```python
"proxy.enabled"
"accstack.api_key"
"mail.smsbower.alias_limit"
"mail.accstack.alias_limit"
```

Them `accstack.api_key` vao `_SECRET_KEYS`; `DATA_MIGRATIONS[7]` xoa `proxy.rotation_mode`.

- [ ] **Step 4: Kiem tra retention/cascade/masking**

Assert check logs tra dung thu tu, toi da 500; retry clear xoa log; xoa check cascade; `all_known()` chi tra `MASKED_VALUE` cho ca hai API key.

- [ ] **Step 5: Chay GREEN va commit**

```powershell
python test/check_registration_storage.py
git add gpt_reg/db test/check_registration_storage.py
git commit -m "feat: persist rentals proxies and check logs"
```

### Task 3: Danh Tinh Ho So Co Seed

**Files:**
- Create: `gpt_reg/profile_identity.py`
- Create: `test/check_profile_identity.py`
- Modify: `gpt_reg/web/jobs/reg_manager.py`

- [ ] **Step 1: Viet check do cho ba region**

Test API mong muon:

```python
identity = generate_profile_identity("vi", seed="job-123", today=date(2026, 7, 27))
assert identity == generate_profile_identity("vi", seed="job-123", today=date(2026, 7, 27))
assert 18 <= age_on(identity.birthdate, date(2026, 7, 27)) <= 45
assert generate_profile_identity("ko", seed="k").name != identity.name
assert generate_profile_identity("in", seed="i").name.isascii()
```

Test `vi` co dau Unicode, `ko` co Hangul, region la enum nghiem ngat, va phan bo tuoi chi nam trong 18-45.

- [ ] **Step 2: Chay RED**

Run: `python test/check_profile_identity.py`

Expected: FAIL vi module chua ton tai.

- [ ] **Step 3: Implement generator thuan Python**

Dung `hashlib.sha256(seed + namespace)` va `random.Random` cuc bo; danh sach ten co nghia cho Viet Nam, Han Quoc va An Do; nhom tuoi co trong so `18-24`, `25-34`, `35-45`. Khong dung global RNG.

- [ ] **Step 4: Persist mot lan luc tao job**

`RegJobManager.start_batch(..., profile_region: str)` tao `profile_name`/`birthdate` truoc `jobs_repo.create`; `_run_one` doc lai cot nay de lap `SignupRequest`, retry khong sinh lai.

- [ ] **Step 5: Chay GREEN va commit**

```powershell
python test/check_profile_identity.py
python test/check_fingerprint_storage.py
git add gpt_reg/profile_identity.py gpt_reg/web/jobs/reg_manager.py test/check_profile_identity.py
git commit -m "feat: generate stable regional profiles"
```

### Task 4: Proxy Random Co Chon Loc Va Fail-fast

**Files:**
- Modify: `gpt_reg/proxy/format.py`
- Modify: `gpt_reg/proxy/pool.py`
- Modify: `gpt_reg/web/jobs/reg_manager.py`
- Modify: `gpt_reg/web/jobs/check_manager.py`
- Create: `test/check_proxy_selection.py`

- [ ] **Step 1: Viet check do**

Test bon contract: off tra direct; co selected chi chon selected; khong selected thi chon tat ca; dong sai lam `replace_all()` fail va giu DB cu. Patch `secrets.choice` de assert tap ung vien, khong assert thu tu ngau nhien.

- [ ] **Step 2: Chay RED**

Run: `python test/check_proxy_selection.py`

Expected: FAIL vi pool hien dung round-robin/text blob.

- [ ] **Step 3: Implement resolver**

`ProxyPool(lines, enabled=True)` validate bang `materialize_proxy`, chon voi `secrets.choice`; bo rotation mode. `ProxyRepository.replace_all(rows)` nhan `[{"value": normalized, "selected": bool}]`. Khi enabled ma danh sach rong, start job tra loi config ro rang; khi proxy ket noi loi, job error va khong chuyen direct.

- [ ] **Step 4: Wire manager**

Server tao pool tu `proxy.enabled` + bang `proxies`; job giu URL da materialize trong suot attempt. Retry job moi acquire lai mot lan.

- [ ] **Step 5: Chay GREEN va commit**

```powershell
python test/check_proxy_selection.py
python test/check_http_fallback.py
git add gpt_reg/proxy gpt_reg/web/jobs test/check_proxy_selection.py
git commit -m "feat: add selected random proxy routing"
```

### Task 5: Mail Rental Contract Va Provider Adapters

**Files:**
- Create: `gpt_reg/mail/rental.py`
- Create: `gpt_reg/mail/alias.py`
- Create: `gpt_reg/mail/smsbower_rental.py`
- Create: `gpt_reg/mail/accstack.py`
- Modify: `gpt_reg/sms/smsbower.py`
- Modify: `gpt_reg/mail/providers.py`
- Create: `test/check_mail_rental_providers.py`

- [ ] **Step 1: Viet fake-transport checks**

Test contract:

```python
status = provider.status()
rental = provider.rent(product_id=None)
otp = provider.wait_for_otp(rental, alias="base+abc123@gmail.com", timeout_s=30)
provider.prepare_next(rental)
provider.close(rental, success=True)
```

Bao phu SMSBower waiting/success/auth/stock/timeout/cancel/status 2-3-5; AccStack product filter, `/mail`, `/code`, `/rerent`, 401/403/502, va timeout khong retry call tinh phi.

- [ ] **Step 2: Chay RED**

Run: `python test/check_mail_rental_providers.py`

Expected: FAIL vi cac module chua ton tai.

- [ ] **Step 3: Implement contract va alias**

Dataclasses `MailSourceStatus`, `MailRental`, exception typed `MailAuthError`, `MailStockError`, `MailBalanceError`, `MailTimeoutError`; `gmail_alias(base_email, seed, index)` giu domain va tao suffix 6 ky tu lowercase/digit, khong log base email.

- [ ] **Step 4: Implement SMSBower adapter**

Goi official endpoints mail voi `service=dr`, `domain=gmail.com`, `alias=0`; key chi di query upstream va duoc redacted trong exception. Poll co deadline/cancel, `setStatus=5` truoc code moi, `3` khi dong thanh cong, `2` khi huy.

- [ ] **Step 5: Implement AccStack adapter**

Dung base `https://accstack.io/api/v1`, header `X-API-Key`, TLS verify, `follow_redirects=False`; status allowlist tu `/me` va Gmail `kind=rent` products. `rent` va `prepare_next` chi goi mot lan, khong retry khi response khong ro trang thai.

- [ ] **Step 6: Chay GREEN va commit**

```powershell
python test/check_mail_rental_providers.py
python test/check_smsbower.py
python test/check_mail_registry.py
git add gpt_reg/mail gpt_reg/sms/smsbower.py test/check_mail_rental_providers.py
git commit -m "feat: integrate rental mail providers"
```

### Task 6: Gmail Rental Coordinator Va Job Outcomes

**Files:**
- Create: `gpt_reg/web/jobs/rental_coordinator.py`
- Modify: `gpt_reg/web/jobs/reg_manager.py`
- Modify: `gpt_reg/models.py`
- Modify: `gpt_reg/signup.py`
- Modify: `gpt_reg/phases/http_reg.py`
- Create: `test/check_rental_coordinator.py`

- [ ] **Step 1: Viet check do cho lifecycle**

Fake provider va fake signup de assert: `rental_count` la so mailbox thue; moi rental tao alias job lan luot; alias unique; account-exists dung rental; expiry/limit/stop dong dung status; retry tai su dung profile. Test SMSBower va AccStack khong fallback cheo.

- [ ] **Step 2: Chay RED**

Run: `python test/check_rental_coordinator.py`

Expected: FAIL vi coordinator va structured outcome chua co.

- [ ] **Step 3: Them structured outcome**

Them `SignupResult.outcome` voi literal `success|account_exists|failed|cancelled`; HTTP va Browser mapping trang account-exists vao field nay. Coordinator quyet dinh dung bang field, khong parse free text.

- [ ] **Step 4: Implement coordinator**

Moi mailbox ghi `mail_rentals`; sinh alias theo index, tao job day du `source_email`, `rental_id`, profile, fingerprint; sau thanh cong goi `prepare_next` neu alias limit > 1. Bat ky state charge khong ro rang deu danh dau rental error va dung.

- [ ] **Step 5: Chay GREEN va commit**

```powershell
python test/check_rental_coordinator.py
python test/check_http_reg.py
python test/check_signup_fingerprint_flow.py
git add gpt_reg/web/jobs gpt_reg/models.py gpt_reg/signup.py gpt_reg/phases/http_reg.py test/check_rental_coordinator.py
git commit -m "feat: coordinate gmail alias registrations"
```

### Task 7: Check Logs, Sanitizer Va HTTP Checkpoints

**Files:**
- Modify: `gpt_reg/web/jobs/reg_manager.py`
- Modify: `gpt_reg/web/jobs/check_manager.py`
- Modify: `gpt_reg/phases/http_reg.py`
- Create: `test/check_persistent_logs.py`
- Create: `test/check_http_progress.py`

- [ ] **Step 1: Viet regression checks**

Assert secret assignment co dung mot dau dong `]`; check log duoc sanitize truoc DB va SSE; retry clear log cu; moi HTTP flow phat day du `1/10` den `10/10`; branch khong chay phat `skipped` tai dung index.

- [ ] **Step 2: Chay RED**

```powershell
python test/check_persistent_logs.py
python test/check_http_progress.py
```

Expected: FAIL do check log chua persist, sanitizer thua `]`, progress dang 0/9 va nhay buoc.

- [ ] **Step 3: Sua log persistence va sanitizer**

Dung chung `sanitize_job_log_line()` cho check manager; append vao SQLite roi moi emit SSE. Regex assignment an ca bracket dong cua secret cu; khong redact nhan progress.

- [ ] **Step 4: Doi HTTP thanh 10 checkpoints**

Danh so 1 prime, 2 CSRF, 3 authorize URL, 4 OAuth init, 5 identify/register, 6 send/resend OTP, 7 wait OTP, 8 verify OTP, 9 create account/existing session, 10 callback/session. Moi branch goi helper mot lan cho moi index; optional branch ghi `skipped: <reason>`.

- [ ] **Step 5: Chay GREEN va commit**

```powershell
python test/check_persistent_logs.py
python test/check_http_progress.py
python test/check_http_login.py
git add gpt_reg/web/jobs gpt_reg/phases/http_reg.py test/check_persistent_logs.py test/check_http_progress.py
git commit -m "fix: persist logs and report continuous progress"
```

### Task 8: FastAPI Source, Proxy Va Log APIs

**Files:**
- Modify: `gpt_reg/web/server.py`
- Modify: `test/check_job_api.py`
- Create: `test/check_operations_api.py`

- [ ] **Step 1: Viet API checks**

Test `GET /api/mail-sources/status`, `POST /api/jobs/start` source-specific validation, `GET/PUT /api/proxies`, `GET /api/checks/{id}/logs`, 404, no-store, masked key, va 400 cho quantity/profile/product sai.

- [ ] **Step 2: Chay RED**

Run: `python test/check_operations_api.py`

Expected: FAIL 404 cho endpoint moi.

- [ ] **Step 3: Implement route va payload parser**

`outlook` yeu cau `input`; Gmail yeu cau integer `rental_count` 1..stock, AccStack nhan `product_id` tu allowlist. Status response chi gom `configured,balance,currency,price,stock,affordable,products,reason`; route nhay cam gan `Cache-Control: no-store`.

- [ ] **Step 4: Wire proxy va check logs**

PUT proxy parse tat ca dong va commit atomically; GET tra selected count. Check log endpoint xac nhan check ton tai va tra toi da 500 dong.

- [ ] **Step 5: Chay GREEN va commit**

```powershell
python test/check_operations_api.py
python test/check_job_api.py
python test/check_web_security.py
git add gpt_reg/web/server.py test/check_operations_api.py test/check_job_api.py
git commit -m "feat: expose registration operations APIs"
```

### Task 9: Registration UI Va Jobs Layout

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/views/RegistrationView.vue`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/__tests__/views.spec.ts`
- Modify: `frontend/src/__tests__/ui-regressions.spec.ts`

- [ ] **Step 1: Viet Vitest RED**

Mount view va assert ba source tab, Outlook textarea, Gmail quantity/status, AccStack product select chi khi >1 product, profile selector payload, disable rules, label `Hotmail/Outlook`, copy-log giu selection, click workspace dong log, Jobs panel flex-fill khong co `max-height: 282px`.

- [ ] **Step 2: Chay RED**

Run: `npm test -- --run src/__tests__/views.spec.ts src/__tests__/ui-regressions.spec.ts`

Expected: FAIL vi UI chi co Outlook/Gmail placeholder.

- [ ] **Step 3: Implement source controls**

Dung union `outlook|gmail_smsbower|gmail_accstack`; load status khi chon/refresh; quantity stepper co width on dinh; status strip Balance/Price/Stock/Affordable; payload chi gui field cua source hien tai; profile segmented `vi|ko|in`.

- [ ] **Step 4: Sua Jobs va Registration Activity**

Panel/body/list dung flex column + `min-height:0` + `overflow:auto`; bo max-height. Nut header dung Lucide `Clipboard`, copy `logs.join('\n')`, khong dong log; row/click ngoai giu hanh vi dong da co.

- [ ] **Step 5: Chay GREEN va commit**

```powershell
cd frontend
npm test -- --run src/__tests__/views.spec.ts src/__tests__/ui-regressions.spec.ts
git add src
git commit -m "feat: redesign registration source workflow"
```

### Task 10: Check-account Log UI

**Files:**
- Modify: `frontend/src/views/CheckAccountsView.vue`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/__tests__/views.spec.ts`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Viet Vitest RED**

Assert click row goi `/api/checks/{id}/logs`, selected class, `check_log` chi append dung id, click ngoai dong, Clipboard copy khong dong, va empty state khong lam nhay layout.

- [ ] **Step 2: Chay RED**

Run: `npm test -- --run src/__tests__/views.spec.ts`

Expected: FAIL vi view chua co selected log state.

- [ ] **Step 3: Implement selected activity**

Them `selectedCheckId`, `logs`, request race guard, SSE filter va activity panel cung contract Registration. Row action button dung `.stop` de khong vo tinh mo/dong log.

- [ ] **Step 4: Chay GREEN va commit**

```powershell
cd frontend
npm test -- --run src/__tests__/views.spec.ts
git add src/views/CheckAccountsView.vue src/styles.css src/types.ts src/__tests__/views.spec.ts
git commit -m "feat: add persistent account check activity"
```

### Task 11: Settings Redesign Va Ba Ban Dich

**Files:**
- Modify: `frontend/src/views/SettingsView.vue`
- Modify: `frontend/src/i18n.ts`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/__tests__/i18n.spec.ts`
- Modify: `frontend/src/__tests__/preferences.spec.ts`
- Modify: `frontend/src/__tests__/views.spec.ts`

- [ ] **Step 1: Viet Vitest RED**

Assert Appearance/rotation absent; Integrations co SMSBower va AccStack secret inputs/configured state/refresh; Proxy co enabled toggle, parsed rows, checkbox, selected/total, line error; ba locale co cung key; language selected state dat tuong phan dat.

- [ ] **Step 2: Chay RED**

```powershell
cd frontend
npm test -- --run src/__tests__/i18n.spec.ts src/__tests__/preferences.spec.ts src/__tests__/views.spec.ts
```

Expected: FAIL vi Appearance va round-robin van ton tai.

- [ ] **Step 3: Implement settings workspace**

Desktop dung section nav hep trai va content phai; mobile stack. Integrations co hai section khong long card; secret placeholder masked va chi gui khi user thay doi. Proxy editor preview tung dong, selected checkbox, toggle, save loading; response error map vao dung line.

- [ ] **Step 4: Hoan thien vi/en/zh-CN**

Them cung tap key cho registration source/status/profile, check activity, integration/proxy; bo key Appearance/rotation khong dung. Segmented active dung `--accent` tren nen co contrast ro o light/dark.

- [ ] **Step 5: Chay GREEN va commit**

```powershell
cd frontend
npm test -- --run
npm run build
git add src
git commit -m "feat: rebuild settings and translations"
```

### Task 12: Smoke Co Chi Phi, Regression, Visual QA Va Release

**Files:**
- Create: `test/smoke_rental_mail.py`
- Modify only on discovered regression: files owned by Tasks 2-11
- Generated and ignored: `output/playwright/*.png`
- Generated: `gpt_reg/web/static/app/**`

- [ ] **Step 1: Viet smoke runner co guard**

CLI bat buoc `--provider smsbower|accstack --confirm-charge`, ep `rental_count=1`, `alias_limit=1`; doc key tu SQLite; ghi balance before/after-rent/after-OTP va ket qua da redact. Khong import runner nay trong `test/run_all.py`.

- [ ] **Step 2: Chay offline regression**

```powershell
python test/run_all.py
cd frontend
npm test -- --run
npm run build
```

Expected: moi `check_*` pass, moi Vitest pass, `vue-tsc` va Vite build exit 0.

- [ ] **Step 3: Chay pilot mot alias moi provider**

```powershell
python test/smoke_rental_mail.py --provider smsbower --confirm-charge
python test/smoke_rental_mail.py --provider accstack --confirm-charge
```

Expected: moi provider thue dung mot mailbox va thu dung mot alias. Neu upstream 401/403/5xx, output chi ro provider/HTTP class va dung; khong fallback provider, khong lo key/mail/OTP/order. Cap nhat alias limit SQLite: 50 chi khi balance chung minh khong co charge moi; con lai 1.

- [ ] **Step 4: Build static va restart loopback**

Run `setup.bat`, sau do `start.bat`; probe `http://127.0.0.1:2023/`, `/api/settings`, `/api/sse`. Expected root/API 200, SSE stream mo duoc, mot process server on dinh.

- [ ] **Step 5: Playwright visual QA**

Chup Registration, Check acc va Settings o `1440x1000`, `1024x768`, `390x844`; assert khong overflow/overlap, source tabs scroll ngang mobile, Jobs/log scroll ma khong nhay, button text khong tran, settings dung thu tu. Luu anh vao `output/playwright/` (ignored).

- [ ] **Step 6: Final review, commit va push**

```powershell
git status --short
git diff --check
git add gpt_reg frontend/src gpt_reg/web/static test docs .gitignore setup.bat start.bat
git commit -m "feat: complete multi-source registration operations UI"
git push -u origin main
```

Expected: working tree sach, `origin/main` tro den commit da verify, khong co runtime database/log/account/API key trong Git.
