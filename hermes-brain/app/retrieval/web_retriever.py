from __future__ import annotations

from dataclasses import dataclass
from typing import List
import re

from app.net.fetch import fetch_url


@dataclass
class WebChunk:
    url: str
    title: str
    excerpt: str
    score: float


class WebRetriever:
    def __init__(self, max_sources: int = 3, timeout_seconds: int = 10, user_agent: str = "HERMES-Brain/0.1") -> None:
        self.max_sources = max_sources
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def retrieve(self, query: str) -> List[WebChunk]:
        urls = _extract_urls(query)
        if not urls:
            return []

        results: List[WebChunk] = []
        for url in urls[: self.max_sources]:
            fetched = fetch_url(
                url,
                timeout=self.timeout_seconds,
                user_agent=self.user_agent,
            )
            if not fetched or not fetched.text:
                continue
            excerpt = _summarize_text(fetched.text, fetched.title)
            results.append(WebChunk(url=fetched.url, title=fetched.title, excerpt=excerpt, score=1.0))

        return results


def _extract_urls(text: str) -> List[str]:
    return re.findall(r"https?://\S+", text)


def _summarize_text(text: str, title: str, max_chars: int = 800) -> str:
    clean = text.replace("\r\n", "\n").replace("\r", "\n")
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    summary_parts: List[str] = []

    if title:
        summary_parts.append(title.strip())

    if sentences:
        summary_parts.extend(sentences[:5])

    summary = " ".join(part.strip() for part in summary_parts if part.strip())
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3].rstrip() + "..."
    return summary
