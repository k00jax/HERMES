"""Retrieval subpackage."""

from .local_retriever import RetrievedChunk
from .web_retriever import WebChunk

__all__ = ["RetrievedChunk", "WebChunk"]
