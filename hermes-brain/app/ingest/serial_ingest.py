from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Dict, Optional

import serial

from .event_store import EventStore
from .xiao_link import XiaoLink

logger = logging.getLogger("app.ingest.serial_ingest")


class SerialIngestor:
    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        events_dir: Path | None = None,
        feedback: bool = False,
        feedback_port: Optional[str] = None,
        feedback_baud: Optional[int] = None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.events_dir = events_dir or Path("data/events")
        self.store = EventStore(self.events_dir)
        self.feedback = feedback
        self.feedback_port = feedback_port or port
        self.feedback_baud = feedback_baud or baudrate
        self._feedback_link: Optional[XiaoLink] = None
        self._last_state: Optional[str] = None
        self._last_sent: float = 0.0

    def run(self) -> None:
        try:
            with serial.Serial(self.port, self.baudrate, timeout=1) as ser:
                logger.info("Listening on %s at %s baud", self.port, self.baudrate)
                if self.feedback:
                    self._feedback_link = XiaoLink()
                    self._feedback_link.connect(self.feedback_port, self.feedback_baud)
                    self._send_feedback_state("WARMUP")
                while True:
                    line = ser.readline().decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue
                    payload = _parse_line(line)
                    self.store.append(payload)
                    if self.feedback:
                        state = _evaluate_state(payload)
                        self._send_feedback_state(state)
        except serial.SerialException as exc:
            raise RuntimeError(f"Serial port error: {exc}") from exc

    def _send_feedback_state(self, state: str) -> None:
        now = time.monotonic()
        if state == self._last_state and (now - self._last_sent) < 15:
            return
        if not self._feedback_link:
            return

        if state == "BAD":
            self._feedback_link.request("LED RED", wait_ack=False)
        elif state == "FRESH":
            self._feedback_link.request("LED GREEN", wait_ack=False)
        else:
            self._feedback_link.request("LED OFF", wait_ack=False)

        self._last_state = state
        self._last_sent = now


def _parse_line(line: str) -> Dict[str, Any]:
    try:
        data = json.loads(line)
        if isinstance(data, dict):
            return data
        return {"value": data}
    except json.JSONDecodeError:
        pass

    parts = re.split(r"[;,\s]+", line)
    parsed: Dict[str, Any] = {}
    for part in parts:
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        parsed[key.strip()] = _coerce_value(value.strip())

    if parsed:
        return parsed
    return {"raw": line}


def _evaluate_state(payload: Dict[str, Any]) -> str:
    state = payload.get("state")
    if isinstance(state, str):
        return state.upper()
    if not payload:
        return "WARMUP"
    return "FRESH"


def _coerce_value(value: str) -> Any:
    try:
        if value.isdigit():
            return int(value)
        return float(value)
    except ValueError:
        return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HERMES Serial Ingest")
    parser.add_argument("--port", required=True, help="Serial port (e.g., /dev/ttyACM0)")
    parser.add_argument("--baudrate", type=int, default=115200, help="Serial baudrate")
    parser.add_argument("--events-dir", type=str, default="data/events", help="Events directory")
    parser.add_argument("--feedback", action="store_true", help="Enable feedback to XIAO")
    parser.add_argument("--feedback-port", type=str, help="Override feedback port")
    parser.add_argument("--feedback-baud", type=int, help="Override feedback baud")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    ingestor = SerialIngestor(
        port=args.port,
        baudrate=args.baudrate,
        events_dir=Path(args.events_dir),
        feedback=args.feedback,
        feedback_port=args.feedback_port,
        feedback_baud=args.feedback_baud,
    )
    ingestor.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
