"""Collector Facebook group — Playwright + phiên đăng nhập (storage_state) lấy từ biến môi trường.

- Phiên đăng nhập do người dùng xuất trên máy mình (scripts/export_fb_session.py), lưu vào GitHub Secret
  FB_STORAGE_STATE; workflow GitHub Actions nạp vào biến môi trường. KHÔNG dùng mật khẩu, KHÔNG commit phiên.
- Gặp trang đăng nhập / checkpoint / CAPTCHA: dừng và báo lỗi, KHÔNG cố vượt qua.
- Chỉ đọc nhóm mà tài khoản đã là thành viên; cuộn số lần giới hạn (max_scrolls) để tải nhẹ.
- Facebook thay đổi giao diện thường xuyên: selector dưới đây là best-effort, cần kiểm tra lại khi lỗi.
"""
import json
import os
import re

from ..models import PartialSourceError, RawItem, SourceError

BLOCKED = ("/login", "/checkpoint", "two_step_verification", "/recover")

# Chạy trong trình duyệt: trả về danh sách bài trong feed của nhóm.
EXTRACT_JS = r"""
() => {
  const out = [];
  const posts = document.querySelectorAll('div[role="feed"] > div');
  for (const p of posts) {
    let id = null, url = null;
    for (const a of p.querySelectorAll('a[href]')) {
      const m = a.href.match(/\/groups\/[^/?#]+\/(?:posts|permalink)\/(\d+)/)
             || a.href.match(/[?&](?:story_fbid|multi_permalinks)=(\d+)/);
      if (m) { id = m[1]; url = a.href.split('?')[0]; break; }
    }
    const msg = p.querySelector('[data-ad-preview="message"], [data-ad-comet-preview="message"]');
    const text = (msg ? msg.innerText : '').trim();
    const head = p.querySelector('h2, h3, h4');
    const author = head ? head.innerText.trim().split('\n')[0] : null;
    out.push({id, url, text, author, all: (p.innerText || '').slice(0, 4000)});
  }
  return out;
}
"""

_COMMENTS = re.compile(r"(\d+(?:[.,]\d+)?)\s*(K|N)?\s*(?:bình luận|comments?)\b", re.I)


def parse_count(s: str) -> int | None:
    m = _COMMENTS.search(s or "")
    if not m:
        return None
    n = float(m.group(1).replace(",", "."))
    return int(n * 1000) if m.group(2) else int(n)


def to_items(raw: list[dict], group_name: str, source_id: str) -> tuple[list[RawItem], int]:
    """Chuyển kết quả JS thành RawItem. Trả về (items, số bài không đọc được)."""
    items, skipped = {}, 0
    for r in raw:
        text = (r.get("text") or "").strip()
        if not text:
            skipped += 1  # bài chỉ có ảnh/video, hoặc selector không khớp
            continue
        c = parse_count(r.get("all", ""))
        it = RawItem(source="facebook", group_name=group_name, content=text, content_id=r.get("id"),
                     original_url=r.get("url"), author=r.get("author"), source_id=source_id,
                     metrics={"comments": c} if c is not None else None)
        items.setdefault(it.key, it)
    return list(items.values()), skipped


def _load_state(env: str | None) -> dict:
    raw = os.environ.get(env or "", "").strip()
    if not raw:
        raise SourceError(f"Facebook: chưa cấu hình phiên đăng nhập (biến môi trường {env or '?'} trống). "
                          "Phiên chỉ có trong GitHub Actions (secret FB_STORAGE_STATE).")
    try:
        state = json.loads(raw)
        assert isinstance(state, dict) and state.get("cookies")
    except Exception:  # noqa: BLE001 — không in nội dung phiên ra log
        raise SourceError("Facebook: phiên đăng nhập không hợp lệ (không phải storage_state JSON có cookies)") from None
    return state


def collect(cfg: dict) -> list[RawItem]:
    url = cfg.get("url") or ""
    if "facebook.com/groups/" not in url:
        raise SourceError("Facebook: thiếu url nhóm dạng https://www.facebook.com/groups/<id>")
    state = _load_state(cfg.get("storage_state_env"))
    try:
        from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright
    except ImportError:
        raise SourceError("Facebook: chưa cài playwright (pip install playwright && playwright install chromium)") from None

    group_name = cfg.get("group_name") or url
    feed_url = url.rstrip("/") + "/?sorting_setting=CHRONOLOGICAL"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            ctx = browser.new_context(storage_state=state, locale="vi-VN", viewport={"width": 1280, "height": 2000})
            page = ctx.new_page()
            try:
                page.goto(feed_url, wait_until="domcontentloaded", timeout=60000)
            except PWTimeout:
                raise SourceError("Facebook: tải trang nhóm quá thời gian (60s)") from None
            if any(b in page.url for b in BLOCKED) or page.locator('input[name="email"]').count():
                raise SourceError("Facebook: phiên đăng nhập hết hạn hoặc bị checkpoint — cần xuất lại phiên "
                                  "(scripts/export_fb_session.py) và cập nhật secret FB_STORAGE_STATE")
            try:
                page.wait_for_selector('div[role="feed"]', timeout=30000)
            except PWTimeout:
                raise SourceError("Facebook: không thấy feed của nhóm — tài khoản chưa là thành viên, "
                                  "nhóm không tồn tại hoặc giao diện Facebook đã đổi") from None
            for _ in range(int(cfg.get("max_scrolls", 5))):
                for b in page.get_by_role("button", name=re.compile(r"^(Xem thêm|See more)$")).all()[:20]:
                    try:
                        b.click(timeout=2000)
                    except Exception:  # noqa: BLE001
                        pass
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(2500)
            raw = page.evaluate(EXTRACT_JS)
        finally:
            browser.close()

    items, skipped = to_items(raw, group_name, cfg["id"])
    if not items:
        raise SourceError(f"Facebook: không đọc được bài nào ({len(raw)} khối trong feed) — "
                          "có thể giao diện đã đổi, cần cập nhật selector")
    if skipped and skipped > len(items):
        raise PartialSourceError(items, f"Facebook: {skipped}/{len(raw)} bài không đọc được nội dung chữ")
    return items
