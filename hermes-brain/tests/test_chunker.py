from app.retrieval.chunker import chunk_text


def test_chunk_text_limits() -> None:
    text = "Paragraph one.\n\n" + "A" * 2000
    chunks = chunk_text(text, max_chars=500, overlap=50)
    assert chunks
    assert all(len(chunk) <= 500 for chunk in chunks)
