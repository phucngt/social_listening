"""CLI:
  python -m sl collect                      # thu thập + lọc trùng + lọc từ khóa → data/pending.jsonl
  python -m sl prepare                      # (routine) dùng run mới nhất của GitHub Actions, nếu không có thì tự collect
  python -m sl ingest <run_id> <file.jsonl> # nhận kết quả phân tích AI → data/items.jsonl, data/alerts.jsonl
  python -m sl report <run_id>              # in Audit Log (markdown) và lưu runs/<run_id>.md
"""
import json
import sys
from pathlib import Path

from . import pipeline, report


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "collect":
        run = pipeline.collect()
        print(json.dumps({"run_id": run["run_id"], "stats": run["stats"], "errors": run["errors"]},
                         ensure_ascii=False, indent=2))
        return 0
    if cmd == "prepare":
        res = pipeline.prepare()
        run = pipeline.load_run(res["run_id"])
        print(json.dumps({**res, "stats": run["stats"], "errors": run["errors"]}, ensure_ascii=False, indent=2))
        return 0
    if cmd == "ingest" and len(argv) == 3:
        run = pipeline.ingest(argv[1], Path(argv[2]))
        print(json.dumps(run["stats"], ensure_ascii=False, indent=2))
        return 0
    if cmd == "report" and len(argv) == 2:
        md = report.render(argv[1])
        (pipeline.RUNS / f"{argv[1]}.md").write_text(md, encoding="utf-8")
        print(md)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
