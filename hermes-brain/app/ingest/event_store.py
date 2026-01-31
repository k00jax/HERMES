from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import time
from typing import Dict, Any


@dataclass
class Event:
    timestamp: float
    payload: Dict[str, Any]


class EventStore:
    def __init__(self, events_dir: Path) -> None:
        self.events_dir = events_dir
        self.events_dir.mkdir(parents=True, exist_ok=True)

    def append(self, payload: Dict[str, Any]) -> None:
        event = Event(timestamp=time.time(), payload=payload)
        file_path = self.events_dir / "events.jsonl"
        with file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.__dict__, ensure_ascii=False) + "\n")
