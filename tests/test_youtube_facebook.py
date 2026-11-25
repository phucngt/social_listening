import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sl import pipeline
from sl.collectors import facebook, youtube
from sl.models import PartialSourceError, RawItem, SourceError
from sl.util import read_jsonl

from test_pipeline import env  # noqa: F401 — dùng lại fixture

FIX = Path(__file__).parent / "fixtures"


def ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def test_parse_channel_rss():
    items = youtube.parse_channel_rss((FIX / "yt_channel.xml").read_bytes(), "yt")
    assert [i.content_id for i in items] == ["vid1", "vid2"]
    assert items[0].metrics == {"views": 24403} and items[1].metrics is None
    assert items[0].group_name == "YouTube — Kênh Chứng Khoán A"
    assert items[0].key == "youtube:id:vid1"
    assert items[0].original_url == "https://www.youtube.com/watch?v=vid1"


class FakeAPI:
    def __init__(self, key):
        self.key, self.units, self.searches = key, 0, []

    def search(self, q, order, after, n):
        self.searches.append(q)
        self.units += 100
        return ["v1"]

    def videos(self, ids):
        self.units += 1
        return {"v1": {"snippet": {"title": "Yuanta", "description": "d", "channelTitle": "C",
                                   "publishedAt": "2026-11-24T00:00:00Z"},
                       "statistics": {"viewCount": "123", "likeCount": "4"}}}


def test_youtube_api_search_throttled_and_no_key(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "STATE", tmp_path / "st.json")
    cfg = {"id": "yt", "queries": ["Yuanta"], "search_every_hours": 3}
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    with pytest.raises(SourceError, match="Chưa có API key"):
        youtube.collect(cfg, FakeAPI)

    monkeypatch.setenv("YOUTUBE_API_KEY", "SECRET")
    apis = []
    factory = lambda k: apis.append(FakeAPI(k)) or apis[-1]  # noqa: E731
    items = youtube.collect(cfg, factory)
    assert items[0].metrics == {"views": 123, "likes": 4}
    assert youtube.collect(cfg, factory) == []  # lần 2 trong 3 giờ: không gọi search
    assert [a.searches for a in apis] == [["Yuanta"], []]


def test_api_error_hides_key(monkeypatch):
    api = youtube.YouTubeAPI("SECRETKEY")
    monkeypatch.setattr(youtube, "_get", lambda url, timeout=30: (_ for _ in ()).throw(OSError(f"boom {url}")))
    with pytest.raises(youtube.ApiError) as e:
        api.search("x", "date", "2026-01-01T00:00:00Z", 5)
    assert "SECRETKEY" not in str(e.value)


def test_is_hot_and_watching():
    hot = {"min_views": 50000, "min_views_per_hour": 2000, "watch_hours": 24}
    assert pipeline.is_hot({"views": 60000}, ago(30), hot)
    assert pipeline.is_hot({"views": 10000}, ago(2), hot)       # 5000/giờ
    assert not pipeline.is_hot({"views": 10000}, ago(20), hot)
    assert not pipeline.is_hot(None, ago(1), hot)
    assert pipeline._still_watching(ago(3), hot) and not pipeline._still_watching(ago(30), hot)


def test_collect_two_layers_and_watching(env, monkeypatch):  # noqa: F811
    items = [
        RawItem("youtube", "YouTube — A", "Yuanta ra mắt app", "a", ago(1), metrics={"views": 10}),   # lớp 2
        RawItem("youtube", "YouTube — A", "VN-Index lập đỉnh", "b", ago(2), metrics={"views": 90000}),  # lớp 1
        RawItem("youtube", "YouTube — A", "Video mới ít xem", "c", ago(2), metrics={"views": 50}),     # theo dõi
        RawItem("youtube", "YouTube — A", "Video cũ ít xem", "d", ago(30), metrics={"views": 50}),     # đã thấy
    ]
    monkeypatch.setitem(pipeline.COLLECTORS, "fake", lambda cfg: list(items))
    (env / "src.yaml").write_text(
        "sources:\n  - {id: y, type: fake, enabled: true, hot: {min_views: 50000, watch_hours: 24}}\n", encoding="utf-8")
    s = pipeline.collect(env / "src.yaml", env / "kw.yaml")["stats"]
    assert (s["relevant_new_layer2"], s["hot_new_layer1"], s["watching"], s["new"]) == (1, 1, 1, 3)
    pend = {p["content_id"]: p for p in read_jsonl(pipeline.PENDING)}
    assert pend["b"]["layer"] == 1 and pend["b"]["topic_hint"] == "Thị trường" and pend["b"]["hot_reason"]

    items[2].metrics = {"views": 60000}  # video "c" thành hot ở lần sau
    s = pipeline.collect(env / "src.yaml", env / "kw.yaml")["stats"]
    assert s["hot_new_layer1"] == 1 and s["duplicate"] == 3


def test_prepare_prefers_fresh_actions_run(env, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SL_RUNNER", "github_actions")
    r = pipeline.collect(env / "src.yaml", env / "kw.yaml")
    assert r["runner"] == "github_actions"
    res = pipeline.prepare()
    assert res == {"run_id": r["run_id"], "mode": "github_actions", "older_unreported": []}
    (pipeline.RUNS / f"{r['run_id']}.md").write_text("x")
    monkeypatch.setenv("SL_RUNNER", "local")
    assert pipeline.prepare()["mode"] == "local_fallback"


def test_facebook_to_items_and_session_errors(monkeypatch):
    raw = [{"id": "111", "url": "https://www.facebook.com/groups/g/posts/111/", "text": "YSflex lỗi",
            "author": "A", "all": "YSflex lỗi\n1,2K bình luận"},
           {"id": None, "url": None, "text": "", "author": None, "all": "ảnh"}]
    items, skipped = facebook.to_items(raw, "Gieo mầm tích sản", "fb")
    assert skipped == 1 and items[0].key == "facebook:id:111" and items[0].metrics == {"comments": 1200}
    cfg = {"id": "fb", "url": "https://www.facebook.com/groups/g", "storage_state_env": "FB_X"}
    monkeypatch.delenv("FB_X", raising=False)
    with pytest.raises(SourceError, match="chưa cấu hình phiên"):
        facebook.collect(cfg)
    monkeypatch.setenv("FB_X", "không phải json")
    with pytest.raises(SourceError, match="không hợp lệ") as e:
        facebook.collect(cfg)
    assert "không phải json" not in str(e.value)
