"""Lọc từ khóa trước khi gửi cho AI — giảm chi phí LLM."""
import re
from pathlib import Path

import yaml

from .util import normalize_text, strip_accents


class KeywordFilter:
    def __init__(self, groups: dict[str, list[str]]):
        self.patterns: dict[str, list[tuple[str, re.Pattern]]] = {}
        for group, words in (groups or {}).items():
            pats = []
            for w in words or []:
                w_norm = strip_accents(normalize_text(w))
                # ranh giới từ để "vps" không khớp trong "vpsx", "kis" không khớp "kiss"
                pats.append((w, re.compile(r"(?<![a-z0-9])" + re.escape(w_norm) + r"(?![a-z0-9])")))
            self.patterns[group] = pats

    @classmethod
    def load(cls, path: Path) -> "KeywordFilter":
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def match(self, text: str) -> dict[str, list[str]]:
        t = strip_accents(normalize_text(text))
        hits = {}
        for group, pats in self.patterns.items():
            found = [w for w, p in pats if p.search(t)]
            if found:
                hits[group] = found
        return hits

    @staticmethod
    def is_relevant(hits: dict[str, list[str]]) -> bool:
        # Chỉ chuyển AI khi nhắc tới YSVN hoặc đối thủ; từ khóa rủi ro đơn lẻ không đủ.
        return bool(hits.get("ysvn") or hits.get("competitor"))

    @staticmethod
    def topic_hint(hits: dict[str, list[str]]) -> str:
        if hits.get("ysvn"):
            return "YSVN"
        if hits.get("competitor"):
            return "Đối thủ"
        return "Khác"
