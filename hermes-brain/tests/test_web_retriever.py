from unittest.mock import patch

from app.retrieval.web_retriever import WebRetriever
from app.net.fetch import FetchResult


def test_web_retriever_caps_excerpt() -> None:
    long_text = "Sentence. " * 200
    result = FetchResult(url="https://example.com", title="Example", text=long_text)

    with patch("app.retrieval.web_retriever.fetch_url", return_value=result):
        retriever = WebRetriever(max_sources=1)
        chunks = retriever.retrieve("https://example.com")
        assert chunks
        assert len(chunks[0].excerpt) <= 800
