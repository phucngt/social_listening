"""Nhập tay: đọc inbox/*.jsonl do người dùng copy từ nhóm FB/Zalo."""
from ..models import PartialSourceError, RawItem, SourceError
from ..util import ROOT, read_jsonl

REQUIRED = ("source", "group_name", "content")


def collect(cfg: dict) -> list[RawItem]:
    folder = ROOT / cfg.get("path", "inbox")
    if not folder.is_dir():
        raise SourceError(f"Không tìm thấy thư mục {folder.relative_to(ROOT)}")
    items, errors = [], []
    for f in sorted(folder.glob("*.jsonl")):
        try:
            for n, row in enumerate(read_jsonl(f), 1):
                missing = [k for k in REQUIRED if not str(row.get(k) or "").strip()]
                if missing:
                    errors.append(f"{f.name} dòng {n}: thiếu {', '.join(missing)}")
                    continue
                items.append(RawItem(
                    source=str(row["source"]).strip().lower(),
                    group_name=str(row["group_name"]).strip(),
                    content=str(row["content"]).strip(),
                    content_id=(str(row["content_id"]).strip() or None) if row.get("content_id") else None,
                    published_at=row.get("published_at"),
                    original_url=row.get("original_url"),
                    author=row.get("author"),
                    source_id=cfg["id"],
                ))
        except ValueError as e:
            errors.append(str(e))
    if errors:
        raise PartialSourceError(items, "; ".join(errors))
    return items
