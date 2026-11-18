import json
from pathlib import Path

import pytest

from sl import pipeline, report
from sl.collectors.google_news import parse_rss
from sl.filter import KeywordFilter
from sl.util import content_key, read_jsonl

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def env(tmp_path, monkeypatch):
    data, runs, inbox = tmp_path / "data", tmp_path / "runs", tmp_path / "inbox"
    for d in (data, runs, inbox):
        d.mkdir()
    for mod in (pipeline, report):
        for name in ("SEEN", "PENDING", "ITEMS", "ALERTS"):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, data / getattr(pipeline, name).name)
    monkeypatch.setattr(pipeline, "RUNS", runs)
    monkeypatch.setattr("sl.collectors.manual.ROOT", tmp_path)
    (tmp_path / "kw.yaml").write_text("ysvn: [yuanta, ysflex]\ncompetitor: [vps]\nrisk: [lừa đảo]\n", encoding="utf-8")
    (tmp_path / "src.yaml").write_text(
        "sources:\n  - {id: m, type: manual, enabled: true, path: inbox}\n"
        "  - {id: fb, type: facebook, enabled: false}\n", encoding="utf-8")
    return tmp_path


def write_inbox(env, rows):
    with (env / "inbox" / "a.jsonl").open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_keyword_filter_accent_insensitive_and_word_boundary():
    kw = KeywordFilter({"ysvn": ["yuanta"], "competitor": ["vps"], "risk": ["lừa đảo"]})
    assert kw.match("YUANTA bị lua dao?") == {"ysvn": ["yuanta"], "risk": ["lừa đảo"]}
    assert kw.match("vpsx không phải đối thủ") == {}
    assert not kw.is_relevant(kw.match("lừa đảo chung chung"))


def test_content_key_prefers_id_else_normalized_hash():
    assert content_key("facebook", "g", "123", None, "x") == "facebook:id:123"
    a = content_key("zalo", "g", None, "t", "Xin  chào\nYSVN")
    b = content_key("zalo", "g", None, "t", "xin chào ysvn")
    assert a == b


def test_parse_rss():
    items = parse_rss((FIX / "gnews.xml").read_bytes(), "Google News", "gn")
    assert len(items) == 2
    assert items[0].content_id == "guid-a" and items[0].author == "Báo A"
    assert items[0].published_at.startswith("2026-11-17T02:00")


def test_collect_dedup_ingest_report(env):
    write_inbox(env, [
        {"source": "facebook", "group_name": "Nhóm A", "content_id": "p1", "content": "App YSflex treo cả sáng, lừa đảo à"},
        {"source": "facebook", "group_name": "Nhóm A", "content_id": "p2", "content": "Hôm nay thị trường xanh"},
        {"source": "zalo", "group_name": "Nhóm Z", "content": "thiếu content_id nhưng nói về Yuanta"},
        {"source": "zalo", "content": "thiếu group_name"},
    ])
    run1 = pipeline.collect(env / "src.yaml", env / "kw.yaml")
    s = run1["stats"]
    assert (s["sources_configured"], s["collected"], s["new"], s["relevant_new"]) == (1, 3, 3, 2)
    assert run1["sources"][0]["status"] == "partial"  # dòng thiếu group_name

    run2 = pipeline.collect(env / "src.yaml", env / "kw.yaml")
    assert run2["stats"]["new"] == 0 and run2["stats"]["duplicate"] == 3

    pend = list(read_jsonl(pipeline.PENDING))
    k1 = next(p["key"] for p in pend if p.get("content_id") == "p1")
    k2 = next(p["key"] for p in pend if p["source"] == "zalo")
    analysis = env / "analysis.jsonl"
    analysis.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
        {"key": k1, "topic": "YSVN", "sentiment": "Tiêu cực", "priority": "High", "issue_type": "Khiếu nại",
         "summary": "Khách phản ánh app YSflex treo", "reason": "Lỗi hệ thống ảnh hưởng giao dịch", "department": "IT, BCC"},
        {"key": k2, "topic": "YSVN", "sentiment": "Trung lập", "priority": "Urgent", "issue_type": "Thảo luận"},
    ]), encoding="utf-8")
    run2 = pipeline.ingest(run2["run_id"], analysis)
    assert run2["stats"]["analyzed"] == 1 and run2["stats"]["alerts_created"] == 1
    assert run2["stats"]["pending_total"] == 1  # dòng priority sai vẫn chờ
    rec = next(read_jsonl(pipeline.ITEMS))
    for f in ("source", "group_name", "content_id", "published_at", "detected_at", "content",
              "topic", "sentiment", "priority", "original_url", "processed_at"):
        assert f in rec

    md = report.render(run2["run_id"])
    assert "Trạng thái: **PARTIAL**" in md and "Khách phản ánh app YSflex treo" in md
    assert "fb (đang tắt)" in md


def test_all_sources_failed_is_failed(env):
    (env / "src.yaml").write_text("sources:\n  - {id: fb, type: facebook, enabled: true, storage_state_env: NOPE_X}\n",
                                  encoding="utf-8")
    run = pipeline.collect(env / "src.yaml", env / "kw.yaml")
    assert report.status_of(run) == "FAILED"
    assert "Không thể xác nhận có hay không có nội dung mới" in report.render(run["run_id"])


def test_old_items_marked_seen_not_pending(env):
    (env / "src.yaml").write_text("max_age_days: 7\nsources:\n  - {id: m, type: manual, enabled: true, path: inbox}\n",
                                  encoding="utf-8")
    write_inbox(env, [
        {"source": "facebook", "group_name": "A", "content_id": "old", "published_at": "2020-01-01T00:00:00+07:00",
         "content": "Yuanta cũ"},
        {"source": "facebook", "group_name": "A", "content_id": "nodate", "content": "Yuanta không có ngày"},
    ])
    run = pipeline.collect(env / "src.yaml", env / "kw.yaml")
    assert run["stats"]["too_old"] == 1 and run["stats"]["relevant_new"] == 1
