"""Xuất Audit Log tiếng Việt theo mẫu bắt buộc."""
from .pipeline import ALERTS, ITEMS, PENDING, load_run, previous_run
from .util import read_jsonl


def status_of(run: dict) -> str:
    s = run["stats"]
    if not s.get("sources_configured") or s.get("sources_ok", 0) + s.get("sources_partial", 0) == 0:
        return "FAILED"
    if s.get("sources_failed") or s.get("sources_partial") or s.get("pending_total") or run.get("errors"):
        return "PARTIAL"
    return "SUCCESS"


RUNNERS = {"github_actions": "GitHub Actions (có secret YouTube/Facebook)",
           "local": "Routine Claude — thu thập dự phòng tại chỗ (không có secret)"}
LAYER = {1: "[Lớp 1 – tin hot] ", 2: ""}


def _cell(x) -> str:
    return str(x or "—").replace("|", "\\|").replace("\n", " ")


def render(run_id: str) -> str:
    run = load_run(run_id)
    s = run["stats"]
    st = status_of(run)
    items = [r for r in read_jsonl(ITEMS) if r.get("analyzed_in_run") == run_id]
    pending = list(read_jsonl(PENDING))
    alerts = [a for a in read_jsonl(ALERTS) if a.get("run_id") == run_id]
    prev = previous_run(run_id)
    L = [f"# Audit Log — YSVN Social Listening — run `{run_id}`", "", "## A. Trạng thái thực thi", ""]
    L += [
        f"- Thời gian bắt đầu: {run['started_at']}",
        f"- Thời gian kết thúc: {run.get('finished_at') or run.get('finished_collect_at')}",
        f"- Trạng thái: **{st}**",
        f"- Thu thập bởi: {RUNNERS.get(run.get('runner'), run.get('runner') or 'local')}",
        f"- Số nguồn được cấu hình: {s.get('sources_configured', 0)}",
        f"- Số nguồn truy cập thành công: {s.get('sources_ok', 0)}"
        + (f" (+{s['sources_partial']} một phần)" if s.get("sources_partial") else ""),
        f"- Số nguồn thất bại: {s.get('sources_failed', 0)}",
        f"- Số nội dung thu thập: {s.get('collected', 0)}",
        f"- Số nội dung mới: {s.get('new', 0)} (chuyển AI: {s.get('relevant_new', 0)}"
        + (f" — lớp 2 YSVN/đối thủ: {s.get('relevant_new_layer2', 0)}, lớp 1 tin hot: {s.get('hot_new_layer1', 0)}"
           if "hot_new_layer1" in s else "") + ")"
        + (f"; {s['watching']} video/bài còn mới đang theo dõi số liệu, chưa tính là đã thấy" if s.get("watching") else ""),
        f"- Số nội dung trùng lặp: {s.get('duplicate', 0)}"
        + (f" (ngoài ra {s['too_old']} nội dung cũ hơn max_age_days, chỉ đánh dấu đã thấy)" if s.get("too_old") else ""),
        f"- Số nội dung được AI phân tích: {s.get('analyzed', 0)}",
        f"- Số cảnh báo đã gửi thành công: {s.get('alerts_delivered', 0)}"
        + (f" ({s.get('alerts_created', 0)} cảnh báo High/Critical chỉ ghi vào data/alerts.jsonl — chưa có kênh notification)"
           if s.get("alerts_created") else ""),
        f"- Số lỗi phát sinh: {len(run.get('errors', []))}",
        "",
    ]
    if st == "FAILED":
        L += ["> Không thể xác nhận có hay không có nội dung mới do lỗi truy cập nguồn.", ""]
    elif st == "SUCCESS" and not s.get("relevant_new"):
        L += ["> Không phát hiện nội dung mới trong phạm vi các nguồn đã kiểm tra.", ""]

    L += ["## B. Danh sách thông tin mới", "",
          "_Sentiment/Priority là kết quả AI suy luận; nội dung và URL là dữ liệu thực tế thu thập._", "",
          "| Thời gian | Nguồn | Nội dung tóm tắt | Sentiment | Priority | URL |", "|---|---|---|---|---|---|"]
    order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    for r in sorted(items, key=lambda r: order.get(r["priority"], 9)):
        L.append(f"| {_cell(r.get('published_at'))} | {_cell(r['source'])} / {_cell(r['group_name'])} | "
                 f"{LAYER.get(r.get('layer', 2), '')}{_cell(r.get('summary') or r['content'][:160])} | {_cell(r['sentiment'])} | "
                 f"{_cell(r['priority'])} | {_cell(r.get('original_url'))} |")
    if not items:
        L.append("| — | — | Không có bản ghi được phân tích trong lần chạy này | — | — | — |")
    hi = [r for r in items if r["priority"] in ("High", "Critical")]
    if hi:
        L += ["", "### Chi tiết High/Critical", ""]
        for r in hi:
            L.append(f"- **{r['priority']}** — {_cell(r.get('summary'))}  \n  Lý do: {_cell(r.get('reason'))}  \n"
                     f"  Bộ phận đề xuất: {_cell(r.get('department'))}  \n  URL: {_cell(r.get('original_url'))}")

    L += ["", "## C. Nhật ký lỗi", ""]
    if run.get("errors"):
        L += ["| Thời điểm | Thành phần | Nguyên nhân | Ảnh hưởng |", "|---|---|---|---|"]
        for e in run["errors"]:
            L.append(f"| {_cell(e['at'])} | {_cell(e['component'])} | {_cell(e['cause'])} | {_cell(e['impact'])} |")
    else:
        L.append("Không có lỗi.")

    L += ["", "## D. Đánh giá chất lượng", ""]
    skipped = [x["id"] for x in run["sources"] if not x["enabled"]]
    failed = [x["id"] for x in run["sources"] if x["status"] in ("failed", "partial")]
    L.append(f"- Nguồn chưa được kiểm tra: {', '.join(failed + [f'{i} (đang tắt)' for i in skipped]) or 'Không'}")
    L.append("- Nguy cơ bỏ sót: " + ("Có — " + ", ".join(failed) + " lỗi" if failed else
             "Thấp với nguồn đã bật; các nhóm Facebook/Zalo chưa được theo dõi tự động" if skipped else "Thấp"))
    sess = [e for e in run.get("errors", []) if "phiên" in e["cause"] or "đăng nhập" in e["cause"]]
    L.append(f"- Dấu hiệu session hết hạn / chưa cấu hình: {'Có — ' + '; '.join(e['component'] for e in sess) if sess else 'Không phát hiện'}")
    L.append(f"- Nội dung chưa phân loại: {len(pending)} bản ghi còn trong data/pending.jsonl")
    if prev:
        notes = []
        ps = {x["id"]: x for x in prev["sources"]}
        for x in run["sources"]:
            p = ps.get(x["id"])
            if p and p["status"] == "ok" and x["status"] != "ok" and x["enabled"]:
                notes.append(f"{x['id']} chuyển từ ok sang {x['status']}")
            if p and p.get("collected", 0) > 0 and x["status"] == "ok" and x["collected"] == 0:
                notes.append(f"{x['id']} thu được 0 nội dung (lần trước {p['collected']})")
        L.append(f"- Bất thường so với lần chạy trước (`{prev['run_id']}`): {'; '.join(notes) or 'Không'}")
    else:
        L.append("- Bất thường so với lần chạy trước: Không có lần chạy trước để so sánh")
    return "\n".join(L) + "\n"
