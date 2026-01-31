from __future__ import annotations

from typing import List
import re


def _split_long(text: str, max_chars: int, overlap: int) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(0, end - overlap)
    return chunks


def chunk_text(text: str, max_chars: int = 900, overlap: int = 120) -> List[str]:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for paragraph in paragraphs:
        for part in _split_long(paragraph, max_chars, overlap):
            part_len = len(part)
            if current_len + part_len + (2 if current else 0) > max_chars and current:
                chunks.append("\n\n".join(current).strip())
                current = []
                current_len = 0
            current.append(part)
            current_len += part_len + (2 if current_len else 0)

    if current:
        chunks.append("\n\n".join(current).strip())

    return [c for c in chunks if c]
