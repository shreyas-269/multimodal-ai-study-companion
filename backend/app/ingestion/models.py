from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ParsedPdf:
    page_count: int
    page_labels: list[str | None]
    licence_pages: list[int]
    chunks: list[dict[str, Any]]
