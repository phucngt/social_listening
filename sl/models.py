from dataclasses import asdict, dataclass, field

from .util import content_key, now_iso


@dataclass
class RawItem:
    """Nội dung thô do collector thu được (chưa phân tích)."""
    source: str            # google_news | manual | facebook | zalo ...
    group_name: str
    content: str
    content_id: str | None = None
    published_at: str | None = None
    original_url: str | None = None
    author: str | None = None
    source_id: str = ""    # id nguồn trong config/sources.yaml
    detected_at: str = field(default_factory=now_iso)

    @property
    def key(self) -> str:
        return content_key(self.source, self.group_name, self.content_id, self.published_at, self.content)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d


class SourceError(Exception):
    """Lỗi truy cập một nguồn — được ghi vào nhật ký lỗi, không làm dừng cả lần chạy."""


class PartialSourceError(SourceError):
    """Nguồn trả về một phần dữ liệu kèm lỗi (ví dụ một số query thất bại)."""

    def __init__(self, items: list, message: str):
        super().__init__(message)
        self.items = items
