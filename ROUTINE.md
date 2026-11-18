# Quy trình cho routine Claude (chạy theo lịch)

Python lo thu thập, lọc trùng và lọc từ khóa; Claude **chỉ** phân tích các dòng trong `data/pending.jsonl`.

1. `pip install -r requirements.txt`
2. `python -m sl collect` → ghi lại `run_id` in ra.
   - Mỗi nguồn lỗi được ghi vào `runs/<run_id>.json`, không làm dừng cả lần chạy.
3. Nếu `data/pending.jsonl` có dòng: đọc từng dòng, phân loại **chỉ dựa trên nội dung có trong dòng**
   (không suy diễn), rồi ghi `data/analysis_<run_id>.jsonl`, mỗi dòng:
   ```json
   {"key": "<key từ pending>", "topic": "YSVN|Đối thủ|Thị trường|Hỗ trợ khách hàng|Khác",
    "sentiment": "Tích cực|Trung lập|Tiêu cực", "priority": "Critical|High|Medium|Low",
    "issue_type": "Khiếu nại|Phản ánh|Tin tức|Thảo luận|Khác", "summary": "tóm tắt 1 câu",
    "reason": "bắt buộc nếu High/Critical", "department": "bắt buộc nếu High/Critical"}
   ```
   Gợi ý mức độ: Critical = cáo buộc lừa đảo/mất tiền/sự cố hệ thống lan rộng/tin pháp lý nghiêm trọng về YSVN;
   High = khiếu nại cụ thể của khách YSVN, tin tiêu cực trên báo lớn; Medium = phản ánh nhỏ, thảo luận tiêu cực
   chung; Low = tin tức/thảo luận trung lập hoặc tích cực.
4. `python -m sl ingest <run_id> data/analysis_<run_id>.jsonl`
   - Dòng sai enum hoặc thiếu reason/department sẽ bị từ chối và ở lại pending.
5. `python -m sl report <run_id>` → Audit Log tiếng Việt, lưu tại `runs/<run_id>.md`.
6. Nếu có cảnh báo High/Critical (`data/alerts.jsonl`, `delivered: false`) hoặc trạng thái FAILED/PARTIAL:
   gửi PushNotification. POC chưa có kênh notification riêng → không được ghi là "đã gửi thành công".
7. Commit `data/`, `runs/` lên branch làm việc và push — đây là cách lưu lịch sử giữa các lần chạy
   (container của routine bị xoá sau mỗi lần chạy).
