from __future__ import annotations

from urllib.request import Request, urlopen


def fetch_url(url: str, timeout: float = 5.0) -> str:
    request = Request(url, headers={"User-Agent": "HERMES-Brain/0.1"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")
