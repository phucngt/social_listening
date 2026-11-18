"""Google News RSS — nguồn công khai, không cần đăng nhập."""
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..models import PartialSourceError, RawItem, SourceError

RSS_URL = "https://news.google.com/rss/search?q={q}&hl=vi&gl=VN&ceid=VN:vi"
UA = "Mozilla/5.0 (compatible; social-listening-poc/0.1)"


def parse_rss(xml_bytes: bytes, group_name: str, source_id: str) -> list[RawItem]:
    root = ET.fromstring(xml_bytes)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = (it.findtext("description") or "").strip()
        guid = (it.findtext("guid") or "").strip() or None
        link = (it.findtext("link") or "").strip() or None
        pub = it.findtext("pubDate")
        published_at = None
        if pub:
            try:
                published_at = parsedate_to_datetime(pub).isoformat()
            except (TypeError, ValueError):
                published_at = None
        src = it.find("source")
        publisher = src.text.strip() if src is not None and src.text else None
        if not title:
            continue
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3].rstrip()
        items.append(RawItem(
            source="google_news",
            group_name=f"{group_name} — {publisher}" if publisher else group_name,
            content=title,  # description của Google News chỉ là HTML lặp lại tiêu đề
            content_id=guid or link,
            published_at=published_at,
            original_url=link,
            author=publisher,
            source_id=source_id,
        ))
    return items


def collect(cfg: dict) -> list[RawItem]:
    out: dict[str, RawItem] = {}
    errors = []
    for q in cfg.get("queries", []):
        url = RSS_URL.format(q=urllib.parse.quote(q))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                for item in parse_rss(r.read(), cfg.get("group_name", "Google News"), cfg["id"]):
                    out.setdefault(item.key, item)  # cùng bài xuất hiện ở nhiều query
        except Exception as e:  # noqa: BLE001 — gom lỗi theo từng query
            errors.append(f"query {q!r}: {type(e).__name__}: {e}")
    if errors and not out:
        raise SourceError("; ".join(errors))
    if errors:
        # Một phần query lỗi: vẫn giữ dữ liệu nhưng báo lỗi để run đánh dấu PARTIAL
        raise PartialSourceError(list(out.values()), "; ".join(errors))
    return list(out.values())
