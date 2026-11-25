"""YouTube — RSS theo kênh (miễn phí, không cần key) + YouTube Data API v3 (tìm theo từ khóa, lấy số liệu).

Không cần đăng nhập YouTube: chỉ đọc dữ liệu công khai.
API key đọc từ biến môi trường (mặc định YOUTUBE_API_KEY), do GitHub Actions nạp từ secret.
Không bao giờ in key ra log: mọi thông báo lỗi đều được che key.

Hạn mức API (10.000 đơn vị/ngày): search.list = 100, videos.list = 1 cho tối đa 50 video.
Mỗi query chỉ được gọi search tối đa 1 lần / search_every_hours (lưu trong data/youtube_state.json).
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from ..models import PartialSourceError, RawItem, SourceError
from ..util import DATA

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
API = "https://www.googleapis.com/youtube/v3/"
UA = "Mozilla/5.0 (compatible; social-listening-poc/0.1)"
STATE = DATA / "youtube_state.json"
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "m": "http://search.yahoo.com/mrss/"}
DESC_MAX = 600


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _content(title: str, desc: str) -> str:
    desc = (desc or "").strip()
    if len(desc) > DESC_MAX:
        desc = desc[:DESC_MAX].rstrip() + "…"
    return f"{title.strip()}\n{desc}".strip()


def _item(vid: str, title: str, desc: str, channel: str, published: str | None, source_id: str,
          metrics: dict | None = None) -> RawItem:
    return RawItem(source="youtube", group_name=f"YouTube — {channel}", content=_content(title, desc),
                   content_id=vid, published_at=published, original_url=f"https://www.youtube.com/watch?v={vid}",
                   author=channel, source_id=source_id, metrics=metrics)


def _comment_item(c: dict, video: RawItem, source_id: str) -> RawItem | None:
    text = (c.get("textOriginal") or c.get("textDisplay") or "").strip()
    if not text or not c.get("id"):
        return None
    title = video.content.split("\n", 1)[0]
    vid = video.content_id
    return RawItem(source="youtube_comment",
                   group_name=f"YouTube — {video.author} — bình luận video: {title[:80]}",
                   content=text, content_id=f"comment:{c['id']}", published_at=c.get("publishedAt"),
                   original_url=f"https://www.youtube.com/watch?v={vid}&lc={c['id']}",
                   author=c.get("authorDisplayName"), source_id=source_id,
                   metrics={"likes": int(c["likeCount"])} if str(c.get("likeCount", "")).isdigit() else None)


def parse_channel_rss(xml_bytes: bytes, source_id: str) -> list[RawItem]:
    root = ET.fromstring(xml_bytes)
    channel = (root.findtext("a:title", default="", namespaces=NS) or "").strip() or "?"
    out = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", namespaces=NS)
        title = e.findtext("a:title", default="", namespaces=NS)
        if not vid or not title:
            continue
        desc = e.findtext("m:group/m:description", default="", namespaces=NS)
        stats = e.find("m:group/m:community/m:statistics", NS)
        metrics = {"views": int(stats.get("views"))} if stats is not None and stats.get("views", "").isdigit() else None
        out.append(_item(vid, title, desc, channel, e.findtext("a:published", namespaces=NS), source_id, metrics))
    return out


class ApiError(Exception):
    pass


class YouTubeAPI:
    def __init__(self, key: str):
        self.key = key
        self.units = 0

    def call(self, endpoint: str, cost: int, **params) -> dict:
        url = API + endpoint + "?" + urllib.parse.urlencode({**params, "key": self.key})
        try:
            data = json.loads(_get(url))
        except urllib.error.HTTPError as e:
            reason = f"HTTP {e.code}"
            try:
                err = json.loads(e.read())["error"]
                reason += f" {err.get('errors', [{}])[0].get('reason', '')}: {err.get('message', '')}"
            except Exception:  # noqa: BLE001
                pass
            raise ApiError(reason.replace(self.key, "***")) from None
        except Exception as e:  # noqa: BLE001
            raise ApiError(f"{type(e).__name__}: {e}".replace(self.key, "***")) from None
        self.units += cost
        return data

    def search(self, q: str, order: str, published_after: str, max_results: int) -> list[str]:
        d = self.call("search", 100, part="id", q=q, type="video", order=order, regionCode="VN",
                      relevanceLanguage="vi", publishedAfter=published_after, maxResults=max_results)
        return [x["id"]["videoId"] for x in d.get("items", []) if x.get("id", {}).get("videoId")]

    def comments(self, vid: str, max_results: int = 100) -> list[dict]:
        """100 bình luận mới nhất (kèm vài phản hồi đầu) = 1 lần gọi = 1 đơn vị hạn mức."""
        try:
            d = self.call("commentThreads", 1, part="snippet,replies", videoId=vid, order="time",
                          maxResults=max_results, textFormat="plainText")
        except ApiError as e:
            if "commentsDisabled" in str(e):
                return []  # video tắt bình luận: không phải lỗi
            raise
        out = []
        for t in d.get("items", []):
            top = t.get("snippet", {}).get("topLevelComment", {})
            out.append({"id": top.get("id") or t.get("id"), **top.get("snippet", {})})
            for r in t.get("replies", {}).get("comments", []):
                out.append({"id": r.get("id"), **r.get("snippet", {})})
        return out

    def videos(self, ids: list[str]) -> dict[str, dict]:
        out = {}
        for i in range(0, len(ids), 50):
            d = self.call("videos", 1, part="snippet,statistics", id=",".join(ids[i:i + 50]), maxResults=50)
            for v in d.get("items", []):
                out[v["id"]] = v
        return out


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _metrics(stats: dict) -> dict:
    return {k: int(stats[s]) for k, s in (("views", "viewCount"), ("likes", "likeCount"),
                                          ("comments", "commentCount")) if str(stats.get(s, "")).isdigit()}


def _dt(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def _drop_excluded(items: dict[str, RawItem], cfg: dict) -> None:
    excl = [x.lower() for x in cfg.get("exclude_channels") or []]
    for vid in [v for v, it in items.items() if any(x in (it.author or "").lower() for x in excl)]:
        del items[vid]


def _collect_comments(api, items: dict[str, RawItem], cfg: dict, state: dict, sid: str,
                      errors: list[str]) -> list[RawItem]:
    """Mỗi video: 100 bình luận mới nhất (1 đơn vị). Bỏ qua video số bình luận không đổi so với lần trước.
    Ưu tiên video nhiều bình luận; tối đa comments_max_videos video mỗi lần chạy."""
    per_video = int(cfg.get("comments_per_video", 100))
    if per_video <= 0:
        return []
    seen_counts = state.setdefault("comment_counts", {})
    cand = [(it.metrics or {}).get("comments", 0) for it in items.values()]
    todo = sorted((it for it, n in zip(items.values(), cand) if n and n != seen_counts.get(it.content_id)),
                  key=lambda it: -(it.metrics or {}).get("comments", 0))[: int(cfg.get("comments_max_videos", 30))]
    out = []
    for it in todo:
        try:
            for c in api.comments(it.content_id, min(per_video, 100)):
                ci = _comment_item(c, it, sid)
                if ci:
                    out.append(ci)
            seen_counts[it.content_id] = it.metrics["comments"]
        except ApiError as e:
            errors.append(f"commentThreads {it.content_id}: {e}")
    live = set(state.get("tracked", {})) | set(items)
    for vid in [v for v in seen_counts if v not in live]:
        del seen_counts[vid]
    return out


def collect(cfg: dict, api_factory=YouTubeAPI) -> list[RawItem]:
    sid = cfg["id"]
    items: dict[str, RawItem] = {}
    errors: list[str] = []

    for cid in cfg.get("channels") or []:
        try:
            for it in parse_channel_rss(_get(RSS_URL.format(cid=urllib.parse.quote(cid))), sid):
                items.setdefault(it.content_id, it)
        except Exception as e:  # noqa: BLE001
            errors.append(f"RSS kênh {cid}: {type(e).__name__}: {e}")

    queries = cfg.get("queries") or []
    key = os.environ.get(cfg.get("api_key_env", "YOUTUBE_API_KEY"), "").strip()
    api = api_factory(key) if key else None
    if queries and not api:
        errors.append(f"Chưa có API key (biến môi trường {cfg.get('api_key_env', 'YOUTUBE_API_KEY')} trống) — "
                      f"bỏ qua {len(queries)} query tìm kiếm. Key chỉ có trong GitHub Actions.")
    if api:
        now = datetime.now(timezone.utc)
        state = _load_state()
        last = state.setdefault("last_search", {})
        every = timedelta(hours=float(cfg.get("search_every_hours", 3)))
        after = (now - timedelta(hours=float(cfg.get("lookback_hours", 48)))).strftime("%Y-%m-%dT%H:%M:%SZ")
        found: list[str] = []
        for q in queries:
            q = {"q": q} if isinstance(q, str) else q
            prev = last.get(q["q"])
            if prev and now - datetime.fromisoformat(prev) < every:
                continue  # tiết kiệm hạn mức; video vẫn được cập nhật số liệu qua RSS/videos.list
            try:
                found += api.search(q["q"], q.get("order", "date"), after, int(q.get("max_results", 25)))
                last[q["q"]] = now.replace(microsecond=0).isoformat()
            except ApiError as e:
                errors.append(f"search {q['q']!r}: {e}")
        # Video tìm được trong lookback_hours được theo dõi tiếp giữa các lần search (cập nhật lượt xem, bình luận)
        tracked = state.setdefault("tracked", {})
        cutoff = now - timedelta(hours=float(cfg.get("lookback_hours", 48)))
        for vid in list(tracked):
            if datetime.fromisoformat(tracked[vid]) < cutoff:
                del tracked[vid]
        ids = list(dict.fromkeys(found + list(items) + list(tracked)))
        if ids:
            try:
                for vid, v in api.videos(ids).items():
                    sn = v.get("snippet", {})
                    items[vid] = _item(vid, sn.get("title", ""), sn.get("description", ""),
                                       sn.get("channelTitle", "?"), sn.get("publishedAt"), sid,
                                       _metrics(v.get("statistics", {})))
            except ApiError as e:
                errors.append(f"videos.list: {e}")
        _drop_excluded(items, cfg)
        for vid, it in items.items():
            dt = _dt(it.published_at)
            if dt and dt >= cutoff:
                tracked.setdefault(vid, dt.replace(microsecond=0).isoformat())
        comment_items = _collect_comments(api, items, cfg, state, sid, errors)
        state["units_last_run"] = api.units
        _save_state(state)

    if not cfg.get("channels") and not queries:
        raise SourceError("YouTube: chưa cấu hình channels hoặc queries")
    _drop_excluded(items, cfg)
    out = list(items.values()) + (comment_items if api else [])
    if errors and not out:
        raise SourceError("; ".join(errors))
    if errors:
        raise PartialSourceError(out, "; ".join(errors))
    return out
