# Web UI

Giao diện vận hành dùng Vue 3, TypeScript, Vite, Tailwind CSS v4, Vue I18n và
Lucide. FastAPI tiếp tục sở hữu API và SQLite runtime settings.

## Phát triển

```powershell
cd frontend
npm ci
npm run dev
```

Dev server chỉ phục vụ frontend; dữ liệu thật cần FastAPI tại cổng cấu hình của
dự án. Bundle production được tạo trực tiếp vào `gpt_reg/web/static/app/`:

```powershell
cd frontend
npm run test:run
npm run build
```

FastAPI đọc `static/app/index.html` rồi phục vụ asset qua `/static/app/`. Web UI
không có lớp đăng nhập riêng; lệnh `gpt-reg web` chỉ nhận host loopback
(`127.0.0.1`, `localhost`, `::1`) và dừng khi host nằm ngoài máy local. HTML đặt
`Cache-Control: no-store`. Không đặt API key trong `localStorage`.

## Quy ước

- Locale: `vi`, `en`, `zh-CN`; lựa chọn được lưu ở `gptreg.locale`.
- Theme: `light`, `dark`; lựa chọn được lưu ở `gptreg.theme`.
- Engine fallback: opt-in theo batch, mặc định tắt và thử engine còn lại theo cả
  hai chiều HTTP↔Browser. Khi bật, concurrency bị chặn theo trần Browser.
- Chỉ `src/services/sse.ts` được tạo `EventSource`; Registration và Check acc
  đăng ký listener theo scope trên cùng một stream.
- Runtime settings luôn đọc/ghi qua `/api/settings` và SQLite repository.
- Chạy `python test/run_all.py` sau khi build để kiểm tra contract backend/UI.
