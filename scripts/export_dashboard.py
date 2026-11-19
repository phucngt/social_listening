"""Xuất dữ liệu cho dashboard tracking (Artifact) ra CSV.

Dùng: python scripts/export_dashboard.py <thư_mục_ra>
Tạo: runs.csv, sources.csv (nguồn của lần chạy mới nhất), items.csv, alerts.csv.
Chỉ đọc runs/*.json và data/*.jsonl, không ghi gì vào repo.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sl.pipeline import ALERTS, ITEMS  # noqa: E402
from sl.report import status_of  # noqa: E402
from sl.util import RUNS, read_jsonl  # noqa: E402

RUN_COLS = ["run_id", "started_at", "status", "sources_configured", "sources_ok", "sources_failed",
            "collected", "new", "duplicate", "relevant_new", "analyzed", "alerts_created", "errors"]
SOURCE_COLS = ["run_id", "source_id", "type", "enabled", "status", "collected", "new"]
ITEM_COLS = ["processed_at", "published_at", "source", "group_name", "topic", "sentiment", "priority",
             "issue_type", "summary", "department", "original_url", "content_id"]
ALERT_COLS = ["created_at", "priority", "summary", "reason", "department", "original_url", "delivered", "run_id"]


def write(path: Path, cols: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RUNS.glob("*.json"))]
    run_rows = []
    for r in runs:
        s = r.get("stats", {})
        run_rows.append({"run_id": r["run_id"], "started_at": r.get("started_at"), "status": status_of(r),
                         **{c: s.get(c, 0) for c in RUN_COLS[3:12]}, "errors": len(r.get("errors", []))})
    write(out / "runs.csv", RUN_COLS, run_rows)

    latest = runs[-1] if runs else {"sources": [], "run_id": ""}
    write(out / "sources.csv", SOURCE_COLS,
          [{"run_id": latest["run_id"], "source_id": x["id"], **x} for x in latest.get("sources", [])])

    items = sorted(read_jsonl(ITEMS), key=lambda x: x.get("processed_at") or "", reverse=True)
    write(out / "items.csv", ITEM_COLS, items)
    alerts = sorted(read_jsonl(ALERTS), key=lambda x: x.get("created_at") or "", reverse=True)
    write(out / "alerts.csv", ALERT_COLS, alerts)
    print(json.dumps({"runs": len(run_rows), "sources": len(latest.get("sources", [])),
                      "items": len(items), "alerts": len(alerts), "out": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dashboard_export")
