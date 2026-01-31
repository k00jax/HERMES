from __future__ import annotations

from urllib.request import Request, urlopen


def internet_available(timeout: float = 2.0) -> bool:
    try:
        request = Request("https://www.example.com", method="HEAD")
        with urlopen(request, timeout=timeout):
            return True
    except Exception:
        return False
