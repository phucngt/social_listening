"""Collector Facebook — CHƯA TRIỂN KHAI.

Chờ: (1) danh sách nhóm được phép theo dõi, (2) tài khoản công ty đã được Pháp chế/IT duyệt.
Khi triển khai: dùng Playwright với storage_state lấy từ biến môi trường (không commit vào repo),
gặp CAPTCHA/checkpoint/đăng nhập lại thì raise SourceError và dừng — không cố vượt qua.
"""
import os

from ..models import SourceError


def collect(cfg: dict):
    env = cfg.get("storage_state_env")
    if not env or not os.environ.get(env):
        raise SourceError(f"Facebook: chưa cấu hình phiên đăng nhập (biến môi trường {env or '?'} trống)")
    raise SourceError("Facebook: collector chưa được triển khai trong POC")
