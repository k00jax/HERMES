"""
Context store: persist MemoryCandidate objects locally and query them.

Format
------
JSONL file: one JSON object per line, one line per stored candidate.
Follows the same convention as event_store.py (already proven on Odroid).

Deduplication
-------------
Candidates have deterministic IDs (bucket_index strings).  The store keeps
a set of IDs seen in the current file and skips appending if an ID already
exists.  On startup, the set is rebuilt by scanning the file.  This is
O(n) on startup but the file is expected to stay small in practice because
we store only candidates above SALIENCE_THRESHOLD, and older entries are
rotated daily.

Rotation
--------
A new JSONL file is created each UTC day.  The filename encodes the date:
    candidates_YYYY-MM-DD.jsonl

Old files beyond MAX_DAYS are deleted on each rotation check.

Thread safety
-------------
This module is written for a single writer process (the daemon).  The
dashboard's context router reads from the same files; reads are safe because
appending to JSONL is atomic at the OS level for lines < PIPE_BUF (~4 KB),
which all our lines satisfy.

Serialisation
-------------
HomeEvent and MemoryCandidate are plain dataclasses — we serialise them
manually to dicts rather than using dataclasses.asdict() to keep the output
stable and explicit.  No third-party libs (no pydantic, no marshmallow).
"""
from __future__ import annotations

import datetime
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .types import HomeEvent, MemoryCandidate

log = logging.getLogger(__name__)

MAX_DAYS = 7


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _event_to_dict(e: HomeEvent) -> Dict[str, Any]:
    return {
        "ts_utc":      e.ts_utc,
        "source":      e.source,
        "kind":        e.kind,
        "value":       e.value,
        "raw_ref":     e.raw_ref,
        "ingested_at": e.ingested_at,
    }


def _candidate_to_dict(c: MemoryCandidate) -> Dict[str, Any]:
    return {
        "candidate_id": c.candidate_id,
        "ts_start":     c.ts_start,
        "ts_end":       c.ts_end,
        "events":       [_event_to_dict(e) for e in c.events],
        "source_mix":   c.source_mix,
        "tags":         c.tags,
        "salience":     c.salience,
        "summary":      c.summary,
        "escalate":     c.escalate,
        "provenance":   c.provenance,
    }


def _dict_to_event(d: Dict[str, Any]) -> HomeEvent:
    return HomeEvent(
        ts_utc=d["ts_utc"],
        source=d["source"],
        kind=d["kind"],
        value=d.get("value", {}),
        raw_ref=d.get("raw_ref"),
        ingested_at=d.get("ingested_at", ""),
    )


def _dict_to_candidate(d: Dict[str, Any]) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=d["candidate_id"],
        ts_start=d["ts_start"],
        ts_end=d["ts_end"],
        events=[_dict_to_event(e) for e in d.get("events", [])],
        source_mix=d.get("source_mix", []),
        tags=d.get("tags", []),
        salience=d.get("salience"),
        summary=d.get("summary"),
        escalate=bool(d.get("escalate", False)),
        provenance=d.get("provenance", {}),
    )


# ---------------------------------------------------------------------------
# ContextStore
# ---------------------------------------------------------------------------

class ContextStore:
    """
    Persistent JSONL store for MemoryCandidate objects.

    Parameters
    ----------
    store_dir
        Directory where candidate files are written.
        Created on init if it does not exist.
    salience_threshold
        Candidates below this score are not persisted.
        Defaults to 0.0 (store everything that reaches the store).
        The daemon typically applies the threshold before calling append().
    max_days
        How many daily files to retain.
    """

    def __init__(
        self,
        store_dir: Path,
        salience_threshold: float = 0.0,
        max_days: int = MAX_DAYS,
    ) -> None:
        self.store_dir = store_dir
        self.salience_threshold = salience_threshold
        self.max_days = max_days
        self.store_dir.mkdir(parents=True, exist_ok=True)

        # In-memory seen set — populated on first write call.
        self._seen_ids: Optional[set[str]] = None

    # --- Internal file path helpers ---

    def _today_path(self) -> Path:
        day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        return self.store_dir / f"candidates_{day}.jsonl"

    def _all_paths(self) -> List[Path]:
        return sorted(self.store_dir.glob("candidates_*.jsonl"))

    # --- ID dedup index ---

    def _load_seen_ids(self) -> set[str]:
        seen: set[str] = set()
        for path in self._all_paths():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            cid = obj.get("candidate_id")
                            if cid:
                                seen.add(str(cid))
                        except json.JSONDecodeError:
                            pass
            except Exception as exc:
                log.warning("context_store: could not read %s: %s", path, exc)
        return seen

    def _ensure_seen(self) -> set[str]:
        if self._seen_ids is None:
            self._seen_ids = _load_seen_ids_from_store(self)
        return self._seen_ids

    # --- Rotation / cleanup ---

    def _rotate(self) -> None:
        paths = self._all_paths()
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=self.max_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        for path in paths:
            # Extract date from filename candidates_YYYY-MM-DD.jsonl
            stem = path.stem  # "candidates_YYYY-MM-DD"
            parts = stem.split("_", 1)
            if len(parts) != 2:
                continue
            date_str = parts[1]
            if date_str < cutoff_str:
                try:
                    path.unlink()
                    log.info("context_store: rotated old file %s", path.name)
                except Exception as exc:
                    log.warning("context_store: could not delete %s: %s", path, exc)

    # --- Public interface ---

    def append(self, candidate: MemoryCandidate) -> bool:
        """
        Persist a candidate if it meets the salience threshold and has not
        been seen before.

        Returns True if the candidate was written, False if skipped.
        """
        if candidate.salience is not None and candidate.salience < self.salience_threshold:
            log.debug(
                "context_store: skip candidate=%s salience=%.3f < threshold=%.3f",
                candidate.candidate_id, candidate.salience, self.salience_threshold,
            )
            return False

        seen = self._ensure_seen()
        if candidate.candidate_id in seen:
            log.debug("context_store: duplicate candidate=%s — skipping", candidate.candidate_id)
            return False

        line = json.dumps(_candidate_to_dict(candidate), ensure_ascii=False)
        path = self._today_path()
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            seen.add(candidate.candidate_id)
            log.debug("context_store: wrote candidate=%s", candidate.candidate_id)
            return True
        except Exception as exc:
            log.error("context_store: write failed for candidate=%s: %s", candidate.candidate_id, exc)
            return False

    def append_all(self, candidates: List[MemoryCandidate]) -> int:
        """Append all candidates.  Returns count of candidates written."""
        written = 0
        for c in candidates:
            if self.append(c):
                written += 1
        self._rotate()
        return written

    def query_recent(
        self,
        limit: int = 20,
        min_salience: float = 0.0,
        tag_filter: Optional[str] = None,
    ) -> List[MemoryCandidate]:
        """
        Return the most recent candidates, newest first.

        Parameters
        ----------
        limit
            Maximum number of candidates to return.
        min_salience
            Only return candidates with salience >= this value.
        tag_filter
            If set, only return candidates that carry this tag.
        """
        results: List[MemoryCandidate] = []
        for path in reversed(self._all_paths()):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    c = _dict_to_candidate(obj)
                except Exception:
                    continue
                if c.salience is not None and c.salience < min_salience:
                    continue
                if tag_filter and tag_filter not in c.tags:
                    continue
                results.append(c)
                if len(results) >= limit:
                    return results
        return results

    def count(self) -> int:
        """Total number of candidates across all retained files."""
        total = 0
        for path in self._all_paths():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    total += sum(1 for line in fh if line.strip())
            except Exception:
                pass
        return total

    def latest_ts(self) -> Optional[str]:
        """ts_end of the most recently stored candidate, or None."""
        for path in reversed(self._all_paths()):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    return str(obj.get("ts_end", ""))
                except Exception:
                    continue
        return None


# Avoid circular reference in _ensure_seen while keeping the method readable.
def _load_seen_ids_from_store(store: ContextStore) -> set[str]:
    return store._load_seen_ids()
