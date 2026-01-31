from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class WebChunk:
    url: str
    title: str
    excerpt: str
    score: float


class WebRetriever:
    def retrieve(self, query: str) -> List[WebChunk]:
        return []
