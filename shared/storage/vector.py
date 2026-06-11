"""Chroma vector store wrapper.

Supports both:
  - HTTP mode (default): connects to docker-compose Chroma on localhost:8001
  - Persistent mode: in-process file-backed Chroma (set CHROMA_PATH env var)
"""
from __future__ import annotations

import os
from typing import Any

import chromadb
from chromadb.config import Settings


class ChromaStore:
    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        path: str | None = None,
    ):
        chroma_path = path or os.getenv("CHROMA_PATH")
        if chroma_path:
            self.client = chromadb.PersistentClient(
                path=chroma_path,
                settings=Settings(anonymized_telemetry=False),
            )
        else:
            self.client = chromadb.HttpClient(
                host=host or os.getenv("CHROMA_HOST", "localhost"),
                port=int(port or os.getenv("CHROMA_PORT", "8001")),
                settings=Settings(anonymized_telemetry=False),
            )

    def get_or_create_collection(self, name: str) -> Any:
        return self.client.get_or_create_collection(name=name)

    def upsert(
        self,
        collection_name: str,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        embeddings: list[list[float]] | None = None,
    ) -> None:
        col = self.get_or_create_collection(collection_name)
        kwargs: dict[str, Any] = {"ids": ids, "documents": documents}
        if metadatas:
            kwargs["metadatas"] = metadatas
        if embeddings:
            kwargs["embeddings"] = embeddings
        col.upsert(**kwargs)

    def query(
        self,
        collection_name: str,
        *,
        query_texts: list[str],
        n_results: int = 5,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        col = self.get_or_create_collection(collection_name)
        kwargs: dict[str, Any] = {"query_texts": query_texts, "n_results": n_results}
        if where:
            kwargs["where"] = where
        return col.query(**kwargs)

    def delete_collection(self, name: str) -> None:
        try:
            self.client.delete_collection(name=name)
        except Exception:
            pass
