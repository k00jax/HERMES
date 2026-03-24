"""Quick smoke test for knowledge base indexing and retrieval.

Run from the hermes-brain directory:
    python -m scripts.test_knowledge

Or directly:
    python scripts/test_knowledge.py
"""

import sys
from pathlib import Path

# Ensure app is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.retrieval.local_index import LocalIndex
from app.retrieval.local_retriever import LocalRetriever

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"
INDEX_PATH = Path(__file__).resolve().parents[1] / "data" / "indexes" / "test_index.json"

# --- Test queries mapped to expected source files ---
TEST_QUERIES = [
    ("What CO2 level is dangerous?", "co2_safety"),
    ("How do I treat a burn?", "first_aid"),
    ("Is 80 percent humidity dangerous?", "humidity"),
    ("How do I purify water in the field?", "water_purification"),
    ("How do I find north without a compass?", "navigation"),
    ("What radio frequency is for emergencies?", "radio_comms"),
    ("How do I build a shelter?", "shelter"),
    ("What temperature causes heatstroke?", "temperature"),
    ("What TVOC level is unhealthy?", "air_quality"),
    ("What is HERMES?", "hermes"),
]


def main():
    print("=" * 60)
    print("HERMES Knowledge Base - Index & Retrieval Test")
    print("=" * 60)

    # Step 1: Build index
    print(f"\n[1] Building index from: {KNOWLEDGE_DIR}")
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    indexer = LocalIndex(index_path=INDEX_PATH, chunk_size=900, chunk_overlap=120)
    indexer.build(str(KNOWLEDGE_DIR))

    # Check index stats
    import json
    with INDEX_PATH.open("r") as f:
        index_data = json.load(f)
    total_chunks = index_data["total_chunks"]
    unique_tokens = len(index_data["doc_freq"])
    sources = set(c["source_path"] for c in index_data["chunks"])

    print(f"    Chunks indexed: {total_chunks}")
    print(f"    Unique tokens:  {unique_tokens}")
    print(f"    Source files:    {len(sources)}")
    for src in sorted(sources):
        count = sum(1 for c in index_data["chunks"] if c["source_path"] == src)
        print(f"      {src} ({count} chunks)")

    # Step 2: Run test queries
    print(f"\n[2] Running {len(TEST_QUERIES)} test queries\n")
    retriever = LocalRetriever(index_path=INDEX_PATH, score_threshold=0.05)

    passed = 0
    failed = 0
    for query, expected_keyword in TEST_QUERIES:
        results = retriever.retrieve(query, k=3)
        top_source = results[0].source_path if results else "(no results)"
        top_score = results[0].score if results else 0.0
        hit = expected_keyword.lower() in top_source.lower()

        status = "PASS" if hit else "MISS"
        if hit:
            passed += 1
        else:
            failed += 1

        print(f"  [{status}] \"{query}\"")
        print(f"         Top: {top_source} (score: {top_score:.4f})")
        if len(results) > 1:
            print(f"         #2:  {results[1].source_path} (score: {results[1].score:.4f})")
        print()

    # Summary
    print("=" * 60)
    print(f"Results: {passed}/{passed + failed} queries matched expected source")
    if failed == 0:
        print("All queries retrieved from expected knowledge files.")
    else:
        print(f"{failed} queries did not match — review scores and content above.")
    print("=" * 60)

    # Cleanup note
    print(f"\nTest index saved at: {INDEX_PATH}")
    print("(This is separate from the production index and safe to delete.)")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
