from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests

from .sanitize import extract_text, extract_title


@dataclass
class FetchResult:
    url: str
    title: str
    text: str


def fetch_url(
    url: str,
    timeout: float = 5.0,
    max_bytes: int = 1_000_000,
    user_agent: str = "HERMES-Brain/0.1",
) -> Optional[FetchResult]:
    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": user_agent}, stream=True)
    except Exception:
        return None

    content_type = response.headers.get("Content-Type", "").lower()
    if "text" not in content_type and "html" not in content_type:
        return None

    data = bytearray()
    try:
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            data.extend(chunk)
            if len(data) > max_bytes:
                break
    except Exception:
        return None

    raw = data.decode("utf-8", errors="ignore")
    title = extract_title(raw)
    text = extract_text(raw)
    return FetchResult(url=url, title=title, text=text)
