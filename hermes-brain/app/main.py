from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

from .config import load_config, AppConfig
from .logging_setup import setup_logging
from .retrieval.local_index import LocalIndex
from .retrieval.local_retriever import LocalRetriever
from .llm.local_llm import LocalLLM

logger = logging.getLogger("app.main")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HERMES Brain - Local Q&A CLI")
    parser.add_argument("question", nargs="*", help="Question to answer")
    parser.add_argument("--reindex", action="store_true", help="Rebuild the local index")
    parser.add_argument("--knowledge-dir", type=str, help="Override knowledge directory")
    parser.add_argument("--index-path", type=str, help="Override index path")
    parser.add_argument("--top-k", type=int, help="Number of chunks to retrieve")
    return parser.parse_args()


def _ensure_index(cfg: AppConfig, reindex: bool) -> None:
    if reindex or not cfg.index_path.exists():
        logger.info("Building local index at %s", cfg.index_path)
        cfg.indexes_dir.mkdir(parents=True, exist_ok=True)
        indexer = LocalIndex(
            index_path=cfg.index_path,
            chunk_size=cfg.chunk_size,
            chunk_overlap=cfg.chunk_overlap,
        )
        indexer.build(str(cfg.knowledge_dir))
    else:
        logger.info("Using existing index at %s", cfg.index_path)


def _format_sources(chunks) -> str:
    if not chunks:
        return "- (none)"
    lines = []
    for chunk in chunks:
        lines.append(f"- {chunk.source_path} [chunk {chunk.chunk_id}] (score {chunk.score:.2f})")
    return "\n".join(lines)


def main() -> int:
    args = _parse_args()
    cfg = load_config()
    setup_logging(cfg.log_level)

    if args.knowledge_dir:
        cfg = cfg.__class__(**{**cfg.__dict__, "knowledge_dir": Path(args.knowledge_dir)})
    if args.index_path:
        cfg = cfg.__class__(**{**cfg.__dict__, "index_path": Path(args.index_path)})
    if args.top_k:
        cfg = cfg.__class__(**{**cfg.__dict__, "top_k": args.top_k})

    question = " ".join(args.question).strip()
    if not question:
        question = input("Question: ").strip()
    if not question:
        logger.error("No question provided.")
        return 1

    _ensure_index(cfg, args.reindex)

    retriever = LocalRetriever(index_path=cfg.index_path, score_threshold=cfg.score_threshold)
    chunks = retriever.retrieve(question, cfg.top_k)

    context = "\n\n".join(
        f"SOURCE: {chunk.source_path} | CHUNK: {chunk.chunk_id}\n{chunk.text}" for chunk in chunks
    )

    llm = LocalLLM(model_path=cfg.model_path, llama_bin=cfg.llama_bin)
    answer = llm.generate(question, context)

    print("Answer:\n" + answer)
    print("\nSources used:\n" + _format_sources(chunks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
