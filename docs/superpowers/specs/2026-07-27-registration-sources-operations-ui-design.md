# Registration Sources And Operations UI Design

## Muc tieu

Mo rong luong dang ky thanh ba nguon mail ro rang, them danh tinh ho so co the
lap lai khi retry, quan ly proxy random co chon loc, va sua trai nghiem log/UI.
Tat ca cau hinh runtime va secret tiep tuc luu trong SQLite; khong hardcode API
key, mailbox thue, OTP hoac proxy credential vao source/bundle/log.

## Pham vi

- Doi nhan `Outlook` thanh `Hotmail/Outlook`.
- Them `Gmail (SMSBower)` va `Gmail (AccStack)` vao man hinh dang ky.
- Hotmail/Outlook nhan combo; hai nguon Gmail nhan so mailbox can thue.
- Them ba bo danh tinh: Viet Nam, Han Quoc, An Do.
- Proxy co cong tac tong, danh sach va checkbox chon tung proxy; luon random.
- Sua panel Jobs, Registration log, HTTP progress va them Check-account log.
- Bo Appearance trong Settings; theme va ngon ngu van nam tai app rail/mobile.
- Chay smoke co chi phi voi mot mailbox va mot alias tren tung provider.

Khong them marketplace/Tong quan AccStack, lich su don hang, mua san pham
`kind=buy`, thue so dien thoai, hay fallback giua cac mail provider.

## Kien truc

### Nguon dang ky

Ba source ID on dinh:

- `outlook`: combo Microsoft Graph hien tai.
- `gmail_smsbower`: mail tam thoi SMSBower, service `dr`, domain `gmail.com`.
- `gmail_accstack`: san pham Gmail `kind=rent` cua AccStack.

`RegJobManager` chi dieu phoi job. API dac thu nam trong hai adapter
`SmsBowerMailRentalProvider` va `AccStackMailRentalProvider`, cung thuc thi mot
contract gom: doc trang thai/ton kho, thue mailbox, doi OTP, chuan bi ma tiep
theo, dong/huỷ rental. Khong adapter nao tu chuyen sang provider khac.

Hotmail giu mo hinh mot combo tao mot job. Gmail nhan `rental_count`; manager
tao rental parent, sau do tao job con khi bat dau tung alias. Quan he nay duoc
luu trong SQLite de reload UI/retry khong mat mailbox/order ID.

### SQLite

Them bang `mail_rentals`:

- `id`, `provider`, `external_id`, `base_email`, `product_id`.
- `status`, `expires_at`, `balance_before`, `balance_after_rent`.
- `alias_count`, `created_at`, `finished_at`, `error`.

Them vao `jobs`:

- `rental_id`, `source_email`, `alias_index`.
- `profile_region`, `profile_name`, `birthdate`.

Them bang `check_logs` co foreign key cascade den `checks`, cung ordering va
retention nhu `job_logs`.

Them bang `proxies`:

- `id`, `value`, `selected`, `created_at`, `updated_at`.
- `value` unique; save danh sach se insert/update/delete trong mot transaction.

Settings moi:

- `proxy.enabled` (`true|false`).
- `accstack.api_key` (secret, response API luon masked).
- `mail.smsbower.alias_limit`, `mail.accstack.alias_limit`.

`proxy.rotation_mode` duoc migrate bo. `sms.smsbower.api_key` tiep tuc dung key
hien co. Schema version tang va migration giu nguyen jobs/settings cu.

## Vong doi Gmail va alias

### SMSBower

1. Doc balance va `getPriceRests(service=dr, domain=gmail.com)`.
2. Goi `getActivation` voi `service=dr`, `domain=gmail.com`, `alias=0` de lay
   mailbox goc va `mailId`.
3. Dang ky bang `local+<suffix>@gmail.com`; suffix la 6 ky tu lowercase/digit,
   sinh tu job seed va khong trung trong rental.
4. Poll `getCode(mailId)`; truoc alias tiep theo goi `setStatus(..., status=5)`.
5. Thanh cong thi dong activation bang status `3`; huy bang status `2` khi
   rental khong con dung duoc.

### AccStack

1. Doc `/me` va `/products`; chi chap nhan product `kind=rent`, ten/mo ta Gmail,
   stock > 0.
2. Goi `/mail?product_id=...` de thue mailbox va luu order/expiry.
3. Tao alias cung quy tac tren, poll `/code?order=...`.
4. Alias tiep theo chi goi `/rerent` khi policy sau smoke cho phep.
5. Khong retry request co tinh phi sau timeout/response khong ro trang thai.

### Dieu kien dung

Moi rental dung khi gap mot trong cac dieu kien:

- OpenAI tra trang thai account da ton tai cho alias dang thu.
- Rental het han, provider het ton kho/so du, nguoi dung stop, hoac OTP het han.
- Dat alias limit cua provider.

Smoke luon override alias limit bang `1`. Ba moc balance duoc ghi: truoc thue,
sau thue, sau khi nhan OTP alias. Sau smoke:

- Neu alias/next-code khong phat sinh them phi: alias limit provider dat `50`.
- Neu can mot giao dich tinh phi moi: alias limit provider giu `1`.
- Neu balance/order state khong xac dinh: fail-fast va giu limit `1`.

Khong suy dien account-exists tu free-text log. Registration phase tra mot
ket qua co cau truc de manager danh dau rental da dung.

## Danh tinh ho so

Them generator thuan Python voi seed cua job:

- `vi`: ho/ten dem/ten Viet Nam co dau.
- `ko`: ho va ten Han Quoc bang Hangul.
- `in`: ten An Do viet Latin.

Ngay sinh hop le tao trong khoang 18-45 tuoi, phan bo co trong so qua cac nhom
18-24, 25-34 va 35-45. `profile_region`, `profile_name`, `birthdate` duoc tao
mot lan luc insert job va tai su dung nguyen ven khi retry/fallback. UI dung
segmented control; mac dinh `vi`.

## Proxy

Settings co cong tac `Dung proxy`, textarea them/sua danh sach, va danh sach da
parse voi checkbox tung dong.

- Cong tac tat: moi luong di truc tiep.
- Cong tac bat, co proxy selected: random chi trong tap selected.
- Cong tac bat, khong proxy selected: random trong toan bo danh sach.
- Moi job giu proxy da chon trong suot attempt; retry job moi co the random lai.
- Proxy sai format tra loi theo dong va khong ghi mot phan cau hinh.
- Proxy loi khong fallback sang direct; mail/auth cung dung proxy cua job.

Backend cung cap `GET/PUT /api/proxies`; response co `selected` va gia tri da
normalize. Random dung `secrets.choice`, khong con round-robin.

## API Web

- `GET /api/mail-sources/status?source=gmail_smsbower|gmail_accstack`.
- `POST /api/jobs/start` nhan mot trong `input` hoac `rental_count`, kem
  `source`, `profile_region`, `product_id` neu AccStack co nhieu san pham.
- `GET /api/checks/{check_id}/logs` tra toi da 500 dong da sanitize.
- `GET/PUT /api/proxies` doc/ghi atomically.

Status source chi tra field allowlist: configured, balance, currency, price,
stock, affordable va danh sach product cong khai. Moi response chua balance,
product hoac log dat `Cache-Control: no-store`. Timeout/upstream auth/stock/balance
giu loi ro rang; khong tra data gia hoac fallback.

## UI/UX

### Registration

- Source segmented control: `Hotmail/Outlook`, `Gmail (SMSBower)`,
  `Gmail (AccStack)`; tren mobile co horizontal scroll, khong ep text.
- Hotmail hien textarea combo. Gmail hien quantity stepper, source health strip
  gom Balance, Price, Stock, Affordable va nut refresh.
- AccStack co product select neu co hon mot Gmail rent product; mot product thi
  tu chon va an control du thua.
- Profile region la segmented control rieng. Run button ghi ro so mailbox se
  thue; disable khi source chua configure, stock thieu hoac quantity vuot stock.
- Khong hien API key, raw order ID, OTP hay mailbox goc trong summary.

### Jobs va logs

- Jobs panel flex-fill chieu cao cua Batch panel; body va list co noi cuon rieng,
  khong con khoang trong do `max-height: 282px`.
- Nut tren Registration Activity dung icon Clipboard va copy toan bo log; click
  lai job hoac click ngoai van dong selection.
- Check-account row co trang thai selected, click tai log cu, nhan `check_log`
  SSE cua dung check, click ngoai dong, va co nut Clipboard.
- Check log duoc sanitize truoc ca SQLite va SSE.
- HTTP trace dung 10 checkpoint lien tuc `1/10..10/10`; nhanh khong chay phat
  marker `skipped`, khong de chuoi 3 -> 6 hoac 7 -> 9.

### Settings

- Xoa Appearance va moi import/CSS/test lien quan. Theme/language van o app rail.
- Desktop: section navigation nho ben trai, noi dung Integrations va Proxy ben
  phai; mobile xep doc, action bar khong sticky che noi dung.
- Integrations co hai section SMSBower/AccStack voi password input, save,
  refresh, configured state va cac chi so mail rental.
- Proxy section co toggle, editor, parsed rows, checkbox, selected/total count;
  nut save co trang thai loading va thong bao loi theo dong.
- Khong long card trong card; heading trong panel giu font compact hien tai.

## Bao mat va xu ly loi

- API key chi luu SQLite va masked boi `_SECRET_KEYS`; khong log/query string o
  route noi bo. Client upstream SMSBower buoc phai gui query theo contract nhung
  exception/log phai redact key.
- AccStack dung `X-API-Key`, base URL co dinh, TLS verify, khong follow redirect,
  khong retry call tinh phi.
- Email/OTP/order ID trong smoke output duoc redact. Session/job log giu sanitizer
  hien tai va sua dau `]` thua sau gia tri redacted.
- Tat ca route van chi kha dung tren loopback theo CLI guard hien co.

## Kiem thu va chap nhan

Backend checks trong `test/`:

- Parser/generator danh tinh: Unicode, tuoi, tinh on dinh theo seed, ba region.
- SMSBower/AccStack adapter: success, waiting, auth, stock, balance, timeout,
  cancellation va redact.
- Rental coordinator: dynamic jobs, alias uniqueness, account-exists stop,
  expiry, limit va structured outcome.
- Proxy repository/resolution: off, selected subset, none-means-all, random,
  invalid line atomicity va fail-fast.
- Check logs: order, retention, sanitizer, 404, retry clear va cascade.
- HTTP progress: du 10 checkpoint, skipped markers dung nhanh.

Frontend Vitest:

- Source-specific input, quantity validation, status strip va product select.
- Profile region payload, proxy settings interactions, Appearance absent.
- Jobs flex-fill contract, Registration copy log, Check select/copy/close/SSE.
- Ban dich `vi`, `en`, `zh-CN` co cung key.

Playwright QA sau build tai 1440x1000, 1024x768 va 390x844: khong overlap,
text/button khong tran, Jobs/log scroll duoc, Settings dung thu tu va source tabs
khong lam layout nhay. Screenshot luu trong `output/playwright/`.

Smoke co chi phi dat tai `test/smoke_rental_mail.py`, mac dinh khong nam trong
`test/run_all.py`. Chay lan luot mot SMSBower va mot AccStack rental voi mot
alias, xac minh OTP/registration va balance delta; dung ngay sau mot alias ke ca
khi provider con quota. Key AccStack do user cung cap duoc ghi truc tiep vao
SQLite truoc smoke, khong them vao file.

Tieu chi hoan thanh:

- `test/run_all.py`, full Vitest va frontend build pass.
- Hai smoke tra ket qua/loi upstream ro rang va khong lo secret.
- Runtime restart tai `127.0.0.1:2023`; root/API/SSE va UI flow duoc probe lai.
- UI desktop/mobile dat cac contract tren va khong con khoang trong Jobs.
