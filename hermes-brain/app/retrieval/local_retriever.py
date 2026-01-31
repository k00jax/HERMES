from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import logging
from typing import Dict, List

from .local_index import compute_idf

logger = logging.getLogger("app.retrieval.local_retriever")


@dataclass
class RetrievedChunk:
    source_path: str
    chunk_id: int
    text: str
    score: float


class LocalRetriever:
    def __init__(self, index_path: Path, score_threshold: float = 0.15) -> None:
        self.index_path = index_path
        self.score_threshold = score_threshold
        self._index = None

    def _load_index(self) -> Dict:
        if self._index is not None:
            return self._index
        if not self.index_path.exists():
            logger.warning("Index not found at %s", self.index_path)
            self._index = {"chunks": [], "doc_freq": {}, "total_chunks": 0}
            return self._index
        with self.index_path.open("r", encoding="utf-8") as handle:
            self._index = json.load(handle)
        return self._index

    def retrieve(self, query: str, k: int) -> List[RetrievedChunk]:
        index = self._load_index()
        chunks = index.get("chunks", [])
        doc_freq = index.get("doc_freq", {})
        total_docs = index.get("total_chunks", len(chunks))
        if not chunks:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scored: List[RetrievedChunk] = []
        for chunk in chunks:
            tokens = chunk.get("tokens", [])
            if not tokens:
                continue
            score = _score_chunk(tokens, query_tokens, doc_freq, total_docs)
            if score >= self.score_threshold:
                scored.append(
                    RetrievedChunk(
                        source_path=chunk.get("source_path", ""),
                        chunk_id=int(chunk.get("chunk_id", 0)),
                        text=chunk.get("text", ""),
                        score=score,
                    )
                )

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:k]


def _tokenize(text: str) -> List[str]:
    import re

    tokens = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    return [token for token in tokens if len(token) > 1]


def _score_chunk(
    tokens: List[str],
    query_tokens: List[str],
    doc_freq: Dict[str, int],
    total_docs: int,
) -> float:
    token_counts: Dict[str, int] = {}
    for token in tokens:
        token_counts[token] = token_counts.get(token, 0) + 1

    score = 0.0
    length = max(len(tokens), 1)
    for token in query_tokens:
        tf = token_counts.get(token, 0) / length
        if tf == 0:
            continue
        idf = compute_idf(doc_freq, total_docs, token)
        score += tf * idf
    return score
