from __future__ import annotations

import logging
import time
from typing import List, Optional

import serial

logger = logging.getLogger("app.ingest.xiao_link")


class XiaoLink:
    def __init__(self) -> None:
        self._serial: Optional[serial.Serial] = None
        self._buffer = ""
        self._port: Optional[str] = None
        self._baud: Optional[int] = None
        self._timeout: float = 1.0
        self._now = time.monotonic

    def connect(self, port: str, baud: int, timeout: float = 1.0) -> None:
        self._port = port
        self._baud = baud
        self._timeout = timeout
        self._open_serial()

    def _open_serial(self) -> None:
        if self._port is None or self._baud is None:
            raise RuntimeError("Serial port not configured")
        try:
            self._serial = serial.Serial(self._port, self._baud, timeout=self._timeout)
        except serial.SerialException as exc:
            logger.warning("Serial connect failed: %s", exc)
            self._serial = None

    def send(self, cmd: str) -> None:
        if not self._serial:
            self._open_serial()
        if not self._serial:
            return
        try:
            self._serial.write(f"CMD {cmd}\n".encode("utf-8"))
        except serial.SerialException as exc:
            logger.warning("Serial write failed: %s", exc)
            self._serial = None

    def request(self, cmd: str, wait_ack: bool = True, ack_timeout: float = 1.5) -> bool:
        self.send(cmd)
        if not wait_ack:
            return True
        deadline = self._now() + ack_timeout
        while self._now() < deadline:
            for line in self.read_lines(nonblocking=True):
                if line.startswith("ACK ") and line[4:].startswith(cmd):
                    return True
                if line.startswith("ERR ") and line[4:].startswith(cmd):
                    return False
            time.sleep(0.01)
        return False

    def read_lines(self, nonblocking: bool = True) -> List[str]:
        if not self._serial:
            self._open_serial()
        if not self._serial:
            return []
        try:
            if nonblocking:
                pending = self._serial.in_waiting or 0
                if pending <= 0:
                    return []
                data = self._serial.read(pending)
            else:
                data = self._serial.read(1)
        except serial.SerialException as exc:
            logger.warning("Serial read failed: %s", exc)
            self._serial = None
            return []

        if not data:
            return []
        self._buffer += data.decode("utf-8", errors="ignore")
        lines: List[str] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.replace("\r", "").strip()
            if line:
                lines.append(line)
        return lines

    def close(self) -> None:
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
