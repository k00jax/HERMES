from __future__ import annotations

from typing import List
from unittest.mock import patch

from app.ingest.xiao_link import XiaoLink


class FakeSerial:
    def __init__(self, *args, **kwargs) -> None:
        self.buffer = bytearray()
        self.written: List[bytes] = []
        self.in_waiting = 0
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)
        if data.startswith(b"CMD PING"):
            self.buffer.extend(b"ACK PING\n")
            self.in_waiting = len(self.buffer)

    def read(self, n: int) -> bytes:
        if n <= 0:
            return b""
        chunk = self.buffer[:n]
        self.buffer = self.buffer[n:]
        self.in_waiting = len(self.buffer)
        return bytes(chunk)

    def close(self) -> None:
        self.closed = True


def test_request_ack_true() -> None:
    with patch("app.ingest.xiao_link.serial.Serial", return_value=FakeSerial()):
        link = XiaoLink()
        link.connect("/dev/ttyACM0", 115200)
        assert link.request("PING") is True


def test_request_timeout_false() -> None:
    with patch("app.ingest.xiao_link.serial.Serial", return_value=FakeSerial()):
        link = XiaoLink()
        link.connect("/dev/ttyACM0", 115200)
        link._now = lambda: 0.0  # type: ignore[assignment]
        assert link.request("LED RED", ack_timeout=0.0) is False


def test_read_lines_splits_buffer() -> None:
    fake = FakeSerial()
    fake.buffer.extend(b"LINE1\nLINE2\nPART")
    fake.in_waiting = len(fake.buffer)
    with patch("app.ingest.xiao_link.serial.Serial", return_value=fake):
        link = XiaoLink()
        link.connect("/dev/ttyACM0", 115200)
        lines = link.read_lines()
        assert lines == ["LINE1", "LINE2"]
