"""Các bước: collect (thu thập + lọc trùng + lọc từ khóa) và ingest (nhận kết quả AI, lưu, cảnh báo)."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .collectors import COLLECTORS
from .filter import KeywordFilter
from .models import PartialSourceError, SourceError
from .util import DATA, ROOT, RUNS, append_jsonl, now_iso, read_jsonl, write_jsonl

SEEN = DATA / "seen.jsonl"          # mọi khóa đã gặp (kể cả không liên quan)
PENDING = DATA / "pending.jsonl"    # nội dung mới, liên quan, chờ AI phân tích
ITEMS = DATA / "items.jsonl"        # bản ghi đã phân tích — kho dữ liệu chính
ALERTS = DATA / "alerts.jsonl"      # cảnh báo High/Critical (kênh file trong POC)

TOPICS = {"YSVN", "Đối thủ", "Thị trường", "Hỗ trợ khách hàng", "Khác"}
SENTIMENTS = {"Tích cực", "Trung lập", "Tiêu cực"}
PRIORITIES = {"Critical", "High", "Medium", "Low"}
ISSUE_TYPES = {"Khiếu nại", "Phản ánh", "Tin tức", "Thảo luận", "Khác"}


def run_path(run_id: str) -> Path:
    return RUNS / f"{run_id}.json"


def load_run(run_id: str) -> dict:
    return json.loads(run_path(run_id).read_text(encoding="utf-8"))


def save_run(run: dict) -> None:
    RUNS.mkdir(exist_ok=True)
    run_path(run["run_id"]).write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")


def previous_run(before_id: str) -> dict | None:
    ids = sorted(p.stem for p in RUNS.glob("*.json") if p.stem < before_id)
    return load_run(ids[-1]) if ids else None


def _too_old(published_at: str | None, max_age_days: int | None) -> bool:
    if not published_at or not max_age_days:
        return False  # không có thời gian đăng thì không loại — tránh bỏ sót
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=7)))  # mặc định giờ Việt Nam
    return dt < datetime.now(timezone.utc) - timedelta(days=max_age_days)


def collect(sources_path: Path = ROOT / "config/sources.yaml",
            keywords_path: Path = ROOT / "config/keywords.yaml") -> dict:
    started = now_iso()
    run_id = started.replace(":", "").replace("-", "").replace("+0000", "Z")
    run = {"run_id": run_id, "started_at": started, "sources": [], "errors": [], "stats": {}}

    try:
        cfg = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
        kw = KeywordFilter.load(keywords_path)
    except Exception as e:  # noqa: BLE001
        run["errors"].append({"at": now_iso(), "component": "config", "cause": f"{type(e).__name__}: {e}",
                              "impact": "Không chạy được lần thu thập nào"})
        run["finished_collect_at"] = now_iso()
        save_run(run)
        return run

    seen = {r["key"] for r in read_jsonl(SEEN)}
    pending_keys = {r["key"] for r in read_jsonl(PENDING)}
    collected, new_seen, new_pending, dup, too_old = 0, [], [], 0, 0
    default_age = cfg.get("max_age_days")

    for src in cfg.get("sources", []):
        entry = {"id": src.get("id"), "type": src.get("type"), "enabled": bool(src.get("enabled")),
                 "status": "skipped", "collected": 0, "new": 0}
        run["sources"].append(entry)
        if not src.get("enabled"):
            continue
        fn = COLLECTORS.get(src.get("type"))
        items = []
        if fn is None:
            entry["status"] = "failed"
            run["errors"].append({"at": now_iso(), "component": src.get("id"),
                                  "cause": f"Loại nguồn không hỗ trợ: {src.get('type')}",
                                  "impact": "Nguồn không được kiểm tra"})
            continue
        try:
            items = fn(src)
            entry["status"] = "ok"
        except PartialSourceError as e:
            items = e.items
            entry["status"] = "partial"
            run["errors"].append({"at": now_iso(), "component": src["id"], "cause": str(e),
                                  "impact": "Thu thập được một phần dữ liệu của nguồn"})
        except SourceError as e:
            entry["status"] = "failed"
            run["errors"].append({"at": now_iso(), "component": src["id"], "cause": str(e),
                                  "impact": "Nguồn không được kiểm tra — có thể bỏ sót nội dung"})
        except Exception as e:  # noqa: BLE001 — một nguồn lỗi không được làm sập cả run
            entry["status"] = "failed"
            run["errors"].append({"at": now_iso(), "component": src["id"], "cause": f"{type(e).__name__}: {e}",
                                  "impact": "Nguồn không được kiểm tra — lỗi không lường trước"})

        entry["collected"] = len(items)
        collected += len(items)
        for it in items:
            k = it.key
            if k in seen or k in pending_keys:
                dup += 1
                continue
            seen.add(k)
            entry["new"] += 1
            d = it.to_dict()
            new_seen.append({"key": k, "source": it.source, "first_seen": d["detected_at"], "run_id": run_id})
            if _too_old(it.published_at, src.get("max_age_days", default_age)):
                too_old += 1  # đánh dấu đã thấy nhưng không phân tích (bài cũ, baseline lần đầu)
                continue
            hits = kw.match(it.content)
            if kw.is_relevant(hits):
                d.update(run_id=run_id, keyword_hits=hits, topic_hint=kw.topic_hint(hits))
                new_pending.append(d)
                pending_keys.add(k)

    append_jsonl(SEEN, new_seen)
    append_jsonl(PENDING, new_pending)
    enabled = [s for s in run["sources"] if s["enabled"]]
    run["stats"] = {
        "sources_configured": len(enabled),
        "sources_ok": sum(s["status"] == "ok" for s in enabled),
        "sources_partial": sum(s["status"] == "partial" for s in enabled),
        "sources_failed": sum(s["status"] == "failed" for s in enabled),
        "collected": collected,
        "new": len(new_seen),
        "duplicate": dup,
        "too_old": too_old,
        "relevant_new": len(new_pending),
        "pending_total": len(pending_keys),
    }
    run["finished_collect_at"] = now_iso()
    save_run(run)
    return run


def _validate(a: dict) -> list[str]:
    errs = []
    for field, allowed in (("topic", TOPICS), ("sentiment", SENTIMENTS),
                           ("priority", PRIORITIES), ("issue_type", ISSUE_TYPES)):
        if a.get(field) not in allowed:
            errs.append(f"{field}={a.get(field)!r} không hợp lệ")
    if a.get("priority") in ("High", "Critical") and not (a.get("reason") and a.get("department")):
        errs.append("High/Critical phải có reason và department")
    return errs


def ingest(run_id: str, analysis_path: Path) -> dict:
    """Nhận kết quả phân loại AI (JSONL, mỗi dòng có 'key'), lưu vào items.jsonl, tạo cảnh báo."""
    run = load_run(run_id)
    pending = {r["key"]: r for r in read_jsonl(PENDING)}
    stored, alerts, invalid, unknown = [], [], [], []
    for a in read_jsonl(analysis_path):
        k = a.get("key")
        if k not in pending:
            unknown.append(k)
            continue
        errs = _validate(a)
        if errs:
            invalid.append({"key": k, "errors": errs})
            continue
        p = pending.pop(k)
        rec = {
            "source": p["source"], "group_name": p["group_name"], "content_id": p.get("content_id") or k,
            "published_at": p.get("published_at"), "detected_at": p["detected_at"], "content": p["content"],
            "topic": a["topic"], "sentiment": a["sentiment"], "priority": a["priority"],
            "issue_type": a["issue_type"], "summary": a.get("summary"), "reason": a.get("reason"),
            "department": a.get("department"), "original_url": p.get("original_url"),
            "processed_at": now_iso(), "key": k, "run_id": p.get("run_id"), "analyzed_in_run": run_id,
            "author": p.get("author"), "analysis_by": "AI (Claude) — kết quả suy luận, cần người kiểm tra",
        }
        stored.append(rec)
        if rec["priority"] in ("High", "Critical"):
            alerts.append({"created_at": now_iso(), "run_id": run_id, "key": k, "priority": rec["priority"],
                           "summary": rec["summary"], "reason": rec["reason"], "department": rec["department"],
                           "original_url": rec["original_url"], "delivery": "file_only",
                           "delivered": False, "note": "Chưa có kênh notification — cảnh báo chỉ ghi file"})
    append_jsonl(ITEMS, stored)
    append_jsonl(ALERTS, alerts)
    write_jsonl(PENDING, pending.values())
    for x in invalid:
        run["errors"].append({"at": now_iso(), "component": "ingest", "cause": f"{x['key']}: {'; '.join(x['errors'])}",
                              "impact": "Bản ghi chưa được lưu, vẫn nằm trong pending"})
    for k in unknown:
        run["errors"].append({"at": now_iso(), "component": "ingest", "cause": f"key không có trong pending: {k}",
                              "impact": "Bỏ qua dòng phân tích"})
    run["stats"].update(analyzed=len(stored), alerts_created=len(alerts), alerts_delivered=0,
                        pending_total=len(pending), invalid_analysis=len(invalid))
    run["finished_at"] = now_iso()
    save_run(run)
    return run
