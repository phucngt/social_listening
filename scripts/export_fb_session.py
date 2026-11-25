"""Xuất phiên đăng nhập Facebook (storage_state) — CHẠY TRÊN MÁY CÁ NHÂN, không chạy trên server.

  pip install playwright && python -m playwright install chromium
  python scripts/export_fb_session.py

Trình duyệt mở ra → tự đăng nhập bằng tài khoản dành cho POC (tự xử lý 2FA nếu có) → mở thử nhóm cần theo dõi
→ quay lại cửa sổ dòng lệnh bấm Enter. File fb.storage_state.json được tạo (đã nằm trong .gitignore).
Copy TOÀN BỘ nội dung file vào GitHub Secret FB_STORAGE_STATE rồi XÓA file.
Không gửi file này qua chat/email: ai có file này đăng nhập được tài khoản.
"""
import json
import sys

from playwright.sync_api import sync_playwright

OUT = "fb.storage_state.json"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(locale="vi-VN")
    page = ctx.new_page()
    page.goto("https://www.facebook.com/login")
    input("Đăng nhập xong và mở thử nhóm, rồi bấm Enter tại đây... ")
    if "/login" in page.url or "/checkpoint" in page.url:
        sys.exit("Trình duyệt vẫn ở trang đăng nhập/checkpoint — chưa đăng nhập xong. Chạy lại script.")
    ctx.storage_state(path=OUT)
    browser.close()

names = {c["name"] for c in json.load(open(OUT, encoding="utf-8"))["cookies"] if "facebook.com" in c["domain"]}
missing = {"c_user", "xs"} - names
if missing:
    sys.exit(f"Thiếu cookie đăng nhập {sorted(missing)} — chưa đăng nhập thành công. Chạy lại script.")
print(f"OK: đã lưu {OUT} (có cookie c_user, xs).")
print("Copy TOÀN BỘ nội dung file vào GitHub Secret FB_STORAGE_STATE, rồi XÓA file. Không gửi file qua chat.")
