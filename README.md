# YSVN Social Listening — POC

Theo dõi thông tin về Chứng khoán Yuanta Việt Nam trên các nguồn được phép, lọc trùng, lọc từ khóa,
để AI phân loại và xuất Audit Log tiếng Việt. Toàn bộ dữ liệu lưu dạng **JSONL** trong `data/`.

## Luồng xử lý

```
collectors (google_news, manual, youtube, facebook, zalo*)   ← GitHub Actions mỗi giờ (có secret)
   → lọc trùng (data/seen.jsonl)  → lọc tuổi bài (max_age_days)
   → lọc 2 lớp: lớp 1 tin hot (lượt xem/bình luận, `hot:`) | lớp 2 YSVN/đối thủ (config/keywords.yaml)
   → data/pending.jsonl  → AI phân loại (Claude)  → python -m sl ingest
   → data/items.jsonl + data/alerts.jsonl  → python -m sl report → runs/<run_id>.md
```
\* chưa triển khai. `youtube` và `facebook` đã có code nhưng đang tắt trong config cho tới khi có secret.

## Secret (GitHub → Settings → Secrets and variables → Actions)

| Secret | Dùng cho | Cách tạo |
|---|---|---|
| `YOUTUBE_API_KEY` | Collector YouTube (tìm theo từ khóa, lượt xem) | Google Cloud Console → bật YouTube Data API v3 → tạo API key |
| `FB_STORAGE_STATE` | Collector Facebook (phiên đăng nhập, không dùng mật khẩu) | Chạy `python scripts/export_fb_session.py` trên máy cá nhân |

Phiên Claude không đọc được secret; chỉ workflow `.github/workflows/collect.yml` đọc được.
Workflow chạy theo lịch chỉ khi file nằm trên branch mặc định của repo.

## File dữ liệu

| File | Nội dung |
|---|---|
| `data/seen.jsonl` | Mọi khóa nội dung đã gặp (kể cả không liên quan) — dùng để lọc trùng |
| `data/pending.jsonl` | Nội dung mới, liên quan, chờ AI phân tích |
| `data/items.jsonl` | Bản ghi đã phân tích: `source, group_name, content_id, published_at, detected_at, content, topic, sentiment, priority, original_url, processed_at` (+ `issue_type, summary, reason, department`) |
| `data/alerts.jsonl` | Cảnh báo High/Critical (POC: chỉ ghi file, `delivered: false`) |
| `runs/<run_id>.json` / `.md` | Nhật ký từng lần chạy và Audit Log |

Khóa lọc trùng: `source:id:<content_id>` nếu có ID; nếu không, hash của nguồn + nhóm + thời gian + nội dung chuẩn hóa.

## Dùng thử

```bash
pip install -r requirements.txt
python -m sl collect                 # in ra run_id
# (Claude) phân loại data/pending.jsonl → data/analysis_<run_id>.jsonl — xem ROUTINE.md
python -m sl ingest <run_id> data/analysis_<run_id>.jsonl
python -m sl report <run_id>
python -m pytest -q
```

Nhập tay dữ liệu từ nhóm Facebook/Zalo: xem `inbox/README.md`.

## Nguyên tắc

- Không commit cookie/phiên đăng nhập (`secrets/`, `*.storage_state.json` đã được `.gitignore`).
- Collector gặp CAPTCHA/checkpoint thì báo lỗi và dừng, không vượt qua.
- Nguồn lỗi không bao giờ được coi là "không có tin mới".
- Kết quả phân loại là suy luận của AI, được đánh dấu `analysis_by` trong từng bản ghi.
