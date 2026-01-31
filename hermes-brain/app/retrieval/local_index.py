from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import logging
import math
import re
from typing import Dict, List

from .chunker import chunk_text

logger = logging.getLogger("app.retrieval.local_index")


@dataclass
class IndexChunk:
    source_path: str
    chunk_id: int
    text: str
    tokens: List[str]


class LocalIndex:
    def __init__(self, index_path: Path, chunk_size: int = 900, chunk_overlap: int = 120) -> None:
        self.index_path = index_path
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def build(self, knowledge_dir: str) -> None:
        knowledge_path = Path(knowledge_dir)
        if not knowledge_path.exists():
            raise FileNotFoundError(f"Knowledge directory not found: {knowledge_path}")

        files = [
            path
            for path in knowledge_path.rglob("*")
            if path.is_file() and path.suffix.lower() in {".txt", ".md"}
        ]

        chunks: List[IndexChunk] = []
        doc_freq: Dict[str, int] = {}
        total_chunks = 0

        for file_path in files:
            try:
                text = file_path.read_text(encoding="utf-8")
            except Exception:
                logger.warning("Failed to read %s", file_path)
                continue

            for idx, chunk in enumerate(chunk_text(text, self.chunk_size, self.chunk_overlap)):
                tokens = _tokenize(chunk)
                if not tokens:
                    continue
                chunks.append(
                    IndexChunk(
                        source_path=str(file_path.relative_to(knowledge_path)),
                        chunk_id=idx,
                        text=chunk,
                        tokens=tokens,
                    )
                )
                total_chunks += 1
                for token in set(tokens):
                    doc_freq[token] = doc_freq.get(token, 0) + 1

        payload = {
            "version": 1,
            "total_chunks": total_chunks,
            "doc_freq": doc_freq,
            "chunks": [chunk.__dict__ for chunk in chunks],
        }

        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with self.index_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)

        logger.info("Indexed %s chunks from %s files", total_chunks, len(files))


def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    return [token for token in tokens if len(token) > 1]


def compute_idf(doc_freq: Dict[str, int], total_docs: int, token: str) -> float:
    df = doc_freq.get(token, 0)
    return math.log((total_docs + 1) / (df + 1)) + 1.0
