from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import time
from typing import Dict, Any, Iterable


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

    def summarize_recent(self, minutes: int = 10) -> str:
        file_path = self.events_dir / "events.jsonl"
        if not file_path.exists():
            return "No recent sensor events."

        cutoff = time.time() - minutes * 60
        events = list(self._load_events_since(file_path, cutoff))
        if not events:
            return "No recent sensor events."

        numeric_stats: Dict[str, Dict[str, float]] = {}
        last_values: Dict[str, Any] = {}
        for event in events:
            payload = event.payload
            for key, value in payload.items():
                if isinstance(value, (int, float)):
                    stats = numeric_stats.setdefault(key, {"min": value, "max": value, "sum": 0.0, "count": 0})
                    stats["min"] = min(stats["min"], float(value))
                    stats["max"] = max(stats["max"], float(value))
                    stats["sum"] += float(value)
                    stats["count"] += 1
                else:
                    last_values[key] = value

        parts = [f"Recent sensor context (last {minutes} min): {len(events)} events."]
        for key, stats in numeric_stats.items():
            avg = stats["sum"] / max(stats["count"], 1)
            parts.append(f"{key}: avg {avg:.2f}, min {stats['min']:.2f}, max {stats['max']:.2f}.")
        for key, value in last_values.items():
            if key in numeric_stats:
                continue
            parts.append(f"{key}: last seen {value}.")

        return " ".join(parts)

    def _load_events_since(self, file_path: Path, cutoff: float) -> Iterable[Event]:
        try:
            with file_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    timestamp = float(data.get("timestamp", 0))
                    payload = data.get("payload", {})
                    if timestamp >= cutoff and isinstance(payload, dict):
                        yield Event(timestamp=timestamp, payload=payload)
        except Exception:
            return
