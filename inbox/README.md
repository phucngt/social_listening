# Nhập tay (manual inbox)

Đặt file `*.jsonl` vào thư mục này, mỗi dòng một bài/bình luận/tin nhắn:

```json
{"source": "facebook", "group_name": "Tên nhóm", "content_id": "id bài nếu có", "published_at": "2026-11-18T09:15:00+07:00", "author": "tuỳ chọn", "content": "Nội dung...", "original_url": "https://..."}
```

- Bắt buộc: `source`, `group_name`, `content`.
- `content_id` không có thì hệ thống tự tạo khóa từ nguồn + thời gian + nội dung chuẩn hóa.
- File đã đọc vẫn để nguyên; lọc trùng đảm bảo không xử lý lại.
