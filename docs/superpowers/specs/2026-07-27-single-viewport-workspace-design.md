# Single-Viewport Operations Workspace

## Mục tiêu

Tối ưu ba màn hình vận hành để desktop và tablet hiển thị toàn bộ vùng chức năng
trong một viewport, không cuộn toàn trang. Mobile giữ kích thước chữ và vùng bấm
dễ dùng, xếp dọc các panel và giới hạn chiều cao các danh sách dài để cuộn nội bộ.

Thay đổi chỉ thuộc frontend layout/CSS. API, dữ liệu SQLite và logic tác vụ giữ nguyên.

## Khung ứng dụng

- Desktop/tablet từ 761px dùng `100dvh`; topbar và workspace chiếm đúng phần chiều
  cao còn lại.
- `body`, app shell và view host không tạo document scroll trên desktop/tablet.
- Workspace dùng CSS Grid với track `minmax(0, ...)` để panel co đúng chiều cao.
- Danh sách, bảng, log, textarea kết quả và vùng proxy là các vùng cuộn nội bộ.
- Khoảng cách, padding và chiều cao header dùng mức compact nhưng giữ cỡ chữ hiện tại.
- Viewport thấp dùng media query theo chiều cao để giảm thêm padding/gap, không thu nhỏ chữ.

## Registration

- Stat strip nằm trên cùng với chiều cao cố định nhỏ.
- Hàng chính gồm Batch và Jobs, cùng chiều cao và chiếm phần lớn không gian.
- Hàng phụ gồm Activity và Results, luôn nhìn thấy trong viewport.
- Batch chỉ cuộn nội bộ khi nguồn Gmail tạo thêm trường.
- Jobs, log và output cuộn độc lập; nội dung động không thay đổi kích thước grid.

## Check Accounts

- Stat strip nằm trên cùng.
- Hàng chính gồm input và bảng kết quả.
- Hàng phụ đặt Phân loại Free/Plus cạnh Log kiểm tra.
- Bảng kết quả, hai output combo và log có overflow nội bộ.
- Hai hàng chia chiều cao còn lại theo tỷ lệ ưu tiên bảng kết quả.

## Settings

- Menu mục lục giữ cột trái.
- Cột nội dung vừa viewport và cuộn nội bộ khi dữ liệu tích hợp hoặc proxy dài.
- Integration giữ dạng metrics compact; proxy editor co theo phần chiều cao còn lại.
- Không thay đổi hành vi lưu khóa, proxy toggle hoặc chọn proxy.

## Responsive

- Từ 761px trở lên: không document scroll, toàn bộ panel chính hiện diện.
- Dưới 761px: bố cục một cột, cho phép workspace cuộn dọc để giữ khả năng đọc.
- Jobs, bảng kết quả, output và log vẫn có giới hạn chiều cao và cuộn nội bộ.
- Bottom navigation có khoảng đệm tương ứng, không che nội dung cuối.

## Kiểm thử

- CSS regression test xác nhận desktop dùng `100dvh`, view host không tràn và các
  grid dùng track co giãn thay cho chiều cao cố định cũ.
- Playwright kiểm tra Registration, Check và Settings tại 1440x900, 1024x768 và
  390x844.
- Desktop/tablet phải có `documentElement.scrollHeight === innerHeight`.
- Mọi kích thước phải không tràn ngang, không cắt chữ trong nút và giữ các vùng
  danh sách/log có thể cuộn.
- Chạy toàn bộ Vitest, build production và backend regression trước khi commit.
