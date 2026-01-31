from pathlib import Path
import tempfile

from app.retrieval.local_index import LocalIndex
from app.retrieval.local_retriever import LocalRetriever


def test_local_retriever_find_match() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        knowledge = base / "knowledge"
        knowledge.mkdir(parents=True, exist_ok=True)
        (knowledge / "sample.txt").write_text("Solar panels convert sunlight to electricity.")

        index_path = base / "indexes" / "local_index.json"
        indexer = LocalIndex(index_path=index_path)
        indexer.build(str(knowledge))

        retriever = LocalRetriever(index_path=index_path, score_threshold=0.0)
        results = retriever.retrieve("sunlight electricity", k=3)
        assert results
        assert results[0].source_path == "sample.txt"
