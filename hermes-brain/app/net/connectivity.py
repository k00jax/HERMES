from __future__ import annotations

import socket


def internet_available(timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=timeout):
            return True
    except OSError:
        return False
