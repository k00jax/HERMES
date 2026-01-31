from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict

import serial

from .event_store import EventStore

logger = logging.getLogger("app.ingest.serial_ingest")


class SerialIngestor:
    def __init__(self, port: str, baudrate: int = 115200, events_dir: Path | None = None) -> None:
        self.port = port
        self.baudrate = baudrate
        self.events_dir = events_dir or Path("data/events")
        self.store = EventStore(self.events_dir)

    def run(self) -> None:
        try:
            with serial.Serial(self.port, self.baudrate, timeout=1) as ser:
                logger.info("Listening on %s at %s baud", self.port, self.baudrate)
                while True:
                    line = ser.readline().decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue
                    payload = _parse_line(line)
                    self.store.append(payload)
        except serial.SerialException as exc:
            raise RuntimeError(f"Serial port error: {exc}") from exc


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
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    ingestor = SerialIngestor(port=args.port, baudrate=args.baudrate, events_dir=Path(args.events_dir))
    ingestor.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
