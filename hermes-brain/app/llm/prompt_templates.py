from __future__ import annotations


def build_prompt(question: str, context: str) -> str:
    return (
        "You are HERMES Brain, an offline-first reasoning system. "
        "Use the provided local context to answer. "
        "If the context is insufficient, say so plainly.\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {question}\n"
        "Answer:"
    )
