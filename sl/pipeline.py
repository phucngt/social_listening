"""Các bước: collect (thu thập + lọc trùng + lọc từ khóa) và ingest (nhận kết quả AI, lưu, cảnh báo)."""
import json
import os
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


def _parse_dt(published_at: str | None) -> datetime | None:
    if not published_at:
        return None
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=7)))  # mặc định giờ Việt Nam
    return dt


def _age_hours(published_at: str | None) -> float | None:
    dt = _parse_dt(published_at)
    return None if dt is None else (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def _too_old(published_at: str | None, max_age_days: int | None) -> bool:
    age = _age_hours(published_at)
    if age is None or not max_age_days:
        return False  # không có thời gian đăng thì không loại — tránh bỏ sót
    return age > max_age_days * 24


def is_hot(metrics: dict | None, published_at: str | None, hot: dict | None) -> str | None:
    """Lớp 1 — "tin hot" theo tương tác. Trả về lý do (chuỗi) nếu hot, None nếu không."""
    if not hot or not metrics:
        return None
    v, c = metrics.get("views"), metrics.get("comments")
    if hot.get("min_views") and v is not None and v >= hot["min_views"]:
        return f"{v} lượt xem ≥ {hot['min_views']}"
    if hot.get("min_comments") and c is not None and c >= hot["min_comments"]:
        return f"{c} bình luận ≥ {hot['min_comments']}"
    age = _age_hours(published_at)
    if hot.get("min_views_per_hour") and v is not None and age is not None:
        rate = v / max(age, 1.0)
        if rate >= hot["min_views_per_hour"]:
            return f"{rate:.0f} lượt xem/giờ ≥ {hot['min_views_per_hour']}"
    return None


def _still_watching(published_at: str | None, hot: dict | None) -> bool:
    """Bài còn mới, chưa hot: chưa đánh dấu "đã thấy" để lần sau đánh giá lại số liệu."""
    age = _age_hours(published_at)
    return bool(hot and hot.get("watch_hours") and age is not None and age < hot["watch_hours"])


def collect(sources_path: Path = ROOT / "config/sources.yaml",
            keywords_path: Path = ROOT / "config/keywords.yaml") -> dict:
    started = now_iso()
    run_id = started.replace(":", "").replace("-", "").replace("+0000", "Z")
    run = {"run_id": run_id, "started_at": started, "runner": os.environ.get("SL_RUNNER", "local"),
           "sources": [], "errors": [], "stats": {}}

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
    done = {r["key"] for r in read_jsonl(ITEMS)}
    pending_rows = [r for r in read_jsonl(PENDING) if r["key"] not in done]
    write_jsonl(PENDING, pending_rows)  # bỏ dòng đã phân tích (có thể bị phục hồi khi merge branch)
    pending_keys = {r["key"] for r in pending_rows}
    collected, new_seen, new_pending, dup, too_old, watching = 0, [], [], 0, 0, 0
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
        hot_cfg = src.get("hot")
        for it in items:
            k = it.key
            if k in seen or k in pending_keys:
                dup += 1
                continue
            old = _too_old(it.published_at, src.get("max_age_days", default_age))
            hits = {} if old else kw.match(it.content)
            # Nhóm từ khóa chỉ xét trong dòng đầu (tiêu đề) — vd. "vps" trong link mở tài khoản ở mô tả video
            if hits and src.get("title_only_groups") and it.source != "youtube_comment":
                title_hits = kw.match(it.content.split("\n", 1)[0])
                hits = {g: w for g, w in hits.items() if g not in src["title_only_groups"] or g in title_hits}
            # Lớp 2 (lọc tinh): nhắc YSVN/đối thủ. Lớp 1 (lọc thô): tin hot theo tương tác.
            hot = None if old else is_hot(it.metrics, it.published_at, hot_cfg)
            layer = 2 if kw.is_relevant(hits) else 1 if hot else 0
            if (not old and not layer and "views" in (it.metrics or {})
                    and _still_watching(it.published_at, hot_cfg)):
                watching += 1
                continue
            seen.add(k)
            entry["new"] += 1
            d = it.to_dict()
            new_seen.append({"key": k, "source": it.source, "first_seen": d["detected_at"], "run_id": run_id})
            if old:
                too_old += 1  # đánh dấu đã thấy nhưng không phân tích (bài cũ, baseline lần đầu)
                continue
            if layer:
                d.update(run_id=run_id, keyword_hits=hits, layer=layer, hot_reason=hot,
                         topic_hint=kw.topic_hint(hits) if layer == 2 else "Thị trường")
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
        "watching": watching,
        "relevant_new": len(new_pending),
        "relevant_new_layer2": sum(d["layer"] == 2 for d in new_pending),
        "hot_new_layer1": sum(d["layer"] == 1 for d in new_pending),
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
            "author": p.get("author"), "metrics": p.get("metrics"), "layer": p.get("layer", 2),
            "hot_reason": p.get("hot_reason"), "analysis_by": "AI (Claude) — kết quả suy luận, cần người kiểm tra",
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


def prepare(max_age_minutes: int = 120) -> dict:
    """Dùng cho routine Claude: lấy run thu thập mới nhất do GitHub Actions tạo (có secret YouTube/Facebook)
    mà chưa có Audit Log. Không có run nào đủ mới thì tự thu thập tại chỗ (dự phòng, không có secret)."""
    reported = {p.stem for p in RUNS.glob("*.md")}
    unreported = sorted(p.stem for p in RUNS.glob("*.json") if p.stem not in reported)
    now = datetime.now(timezone.utc)
    fresh = []
    for rid in unreported:
        r = load_run(rid)
        started = _parse_dt(r.get("started_at"))
        if r.get("runner") == "github_actions" and started and now - started <= timedelta(minutes=max_age_minutes):
            fresh.append(rid)
    if fresh:
        use, mode = fresh[-1], "github_actions"
    else:
        use, mode = collect()["run_id"], "local_fallback"
        unreported.append(use)
    return {"run_id": use, "mode": mode, "older_unreported": [r for r in unreported if r != use]}
