# Quy trình cho routine Claude (chạy theo lịch)

Python lo thu thập, lọc trùng và lọc từ khóa; Claude **chỉ** phân tích các dòng trong `data/pending.jsonl`.

Thu thập chính chạy trong **GitHub Actions** (`.github/workflows/collect.yml`, phút 20 mỗi giờ) vì chỉ ở đó mới có
secret `YOUTUBE_API_KEY` và `FB_STORAGE_STATE`. Routine Claude dùng lại run đó; nếu không có run nào đủ mới thì tự
thu thập dự phòng (không có secret → nguồn YouTube/Facebook báo lỗi, trạng thái PARTIAL là đúng sự thật).

Lọc 2 lớp: **lớp 1** = tin hot theo tương tác (lượt xem/bình luận, cấu hình `hot:` của từng nguồn), dù không nhắc
YSVN; **lớp 2** = nhắc YSVN/đối thủ (config/keywords.yaml). Mỗi dòng pending có `layer` (1 hoặc 2) và `hot_reason`.

0. `bash scripts/sync_state.sh` — **bắt buộc**, lấy `data/` và `runs/` từ branch `claude/*` có commit mới nhất
   (mỗi lần chạy có thể được cấp branch khác nhau). Lỗi bước này → báo FAILED, không chạy tiếp.
1. `pip install -r requirements.txt`
2. `python -m sl prepare` → ghi lại `run_id` và `mode` in ra (`github_actions` hoặc `local_fallback`).
   - Mỗi nguồn lỗi được ghi vào `runs/<run_id>.json`, không làm dừng cả lần chạy.
   - Nếu có `older_unreported` (run của Actions chưa có Audit Log): chạy `python -m sl report <id>` cho từng run đó.
3. Nếu `data/pending.jsonl` có dòng: đọc từng dòng, phân loại **chỉ dựa trên nội dung có trong dòng**
   (không suy diễn), rồi ghi `data/analysis_<run_id>.jsonl`, mỗi dòng:
   ```json
   {"key": "<key từ pending>", "topic": "YSVN|Đối thủ|Thị trường|Hỗ trợ khách hàng|Khác",
    "sentiment": "Tích cực|Trung lập|Tiêu cực", "priority": "Critical|High|Medium|Low",
    "issue_type": "Khiếu nại|Phản ánh|Tin tức|Thảo luận|Khác", "summary": "tóm tắt 1 câu",
    "reason": "bắt buộc nếu High/Critical", "department": "bắt buộc nếu High/Critical"}
   ```
   Dòng `layer: 1` (tin hot thị trường, không nhắc YSVN): thường topic "Thị trường", priority Low/Medium —
   chỉ nâng lên High nếu nội dung có rủi ro rõ ràng cho công ty chứng khoán nói chung.
   Gợi ý mức độ: Critical = cáo buộc lừa đảo/mất tiền/sự cố hệ thống lan rộng/tin pháp lý nghiêm trọng về YSVN;
   High = khiếu nại cụ thể của khách YSVN, tin tiêu cực trên báo lớn; Medium = phản ánh nhỏ, thảo luận tiêu cực
   chung; Low = tin tức/thảo luận trung lập hoặc tích cực.
4. `python -m sl ingest <run_id> data/analysis_<run_id>.jsonl`
   - Dòng sai enum hoặc thiếu reason/department sẽ bị từ chối và ở lại pending.
5. `python -m sl report <run_id>` → Audit Log tiếng Việt, lưu tại `runs/<run_id>.md`.
6. Nếu có cảnh báo High/Critical (`data/alerts.jsonl`, `delivered: false`) hoặc trạng thái FAILED/PARTIAL:
   gửi PushNotification. POC chưa có kênh notification riêng → không được ghi là "đã gửi thành công".
7. Commit `data/`, `runs/` và push lên **branch được cấp cho lần chạy này**. Lần chạy sau sẽ tự lấy lại
   qua bước 0. Push thất bại → báo PARTIAL và gửi notification (lịch sử lọc trùng không được lưu).
