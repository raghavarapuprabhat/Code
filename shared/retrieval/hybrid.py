"""Hybrid retrieval: BM25 + vector + Reciprocal Rank Fusion (§8.9.2).

The chatbot, DocEval, and any downstream consumer call `hybrid_search(...)` instead of
querying Chroma directly. We fetch the vector top-K from Chroma and, in parallel, run a
dependency-free BM25 over the SAME chunk corpus (pulled from Chroma once and cached per
collection), then fuse the two ranked lists with RRF.

RRF score for a doc d:  Σ_over_rankers  1 / (k + rank_r(d))     (k = 60, standard)

This is robust to score-scale differences between lexical and semantic rankers and needs
no reranker model. If BM25 can't run (empty corpus) we fall back to pure vector results,
so the retriever degrades gracefully.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

_RRF_K = 60
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


class BM25:
    """Minimal BM25 (Okapi) over an in-memory corpus. Dependency-free."""

    def __init__(self, corpus_tokens: list[list[str]], *, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus = corpus_tokens
        self.n = len(corpus_tokens)
        self.doc_len = [len(d) for d in corpus_tokens]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.df: dict[str, int] = defaultdict(int)
        self.tf: list[dict[str, int]] = []
        for doc in corpus_tokens:
            seen = set()
            counts: dict[str, int] = defaultdict(int)
            for tok in doc:
                counts[tok] += 1
                if tok not in seen:
                    self.df[tok] += 1
                    seen.add(tok)
            self.tf.append(counts)

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def scores(self, query: str) -> list[float]:
        q_terms = _tokenize(query)
        out = [0.0] * self.n
        for term in q_terms:
            idf = self._idf(term)
            if idf == 0.0:
                continue
            for i in range(self.n):
                f = self.tf[i].get(term, 0)
                if f == 0:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * (self.doc_len[i] / (self.avgdl or 1)))
                out[i] += idf * (f * (self.k1 + 1)) / denom
        return out


# Per-collection corpus cache so we pull all chunks from Chroma only once.
_CORPUS_CACHE: dict[str, dict[str, Any]] = {}


def _load_corpus(store, collection: str) -> dict[str, Any] | None:
    """Pull every chunk from a Chroma collection for BM25. Cached by collection name."""
    if collection in _CORPUS_CACHE:
        return _CORPUS_CACHE[collection]
    try:
        col = store.get_or_create_collection(collection)
        got = col.get()  # all docs
    except Exception:  # noqa: BLE001
        return None
    ids = got.get("ids") or []
    docs = got.get("documents") or []
    metas = got.get("metadatas") or [{}] * len(ids)
    if not ids:
        return None
    entry = {
        "ids": ids,
        "documents": docs,
        "metadatas": metas,
        "bm25": BM25([_tokenize(d) for d in docs]),
    }
    _CORPUS_CACHE[collection] = entry
    return entry


def invalidate_corpus_cache(collection: str | None = None) -> None:
    """Call after re-indexing so BM25 picks up new chunks."""
    if collection is None:
        _CORPUS_CACHE.clear()
    else:
        _CORPUS_CACHE.pop(collection, None)


def _vector_ranked(store, collection: str, query: str, n: int) -> list[tuple[str, str, dict]]:
    """(id, text, meta) in vector-similarity order."""
    try:
        res = store.query(collection, query_texts=[query], n_results=n)
    except Exception:  # noqa: BLE001
        return []
    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    out = []
    for i, d, m in zip(ids, docs, metas):
        out.append((i, d, m or {}))
    return out


def hybrid_search(store, collection: str, query: str, *, n_results: int = 6,
                  fetch_k: int = 20) -> list[dict]:
    """Return fused top-`n_results` hits: [{id, text, meta, rrf_score, vector_rank, bm25_rank}].

    Combines Chroma vector search with BM25 over the same corpus via RRF. Falls back to
    pure vector when BM25 corpus is unavailable.
    """
    vector_hits = _vector_ranked(store, collection, query, fetch_k)

    corpus = _load_corpus(store, collection)
    rank_tables: list[dict[str, int]] = []
    text_by_id: dict[str, str] = {}
    meta_by_id: dict[str, dict] = {}

    # Vector ranking table.
    vec_rank: dict[str, int] = {}
    for rank, (cid, text, meta) in enumerate(vector_hits):
        vec_rank[cid] = rank
        text_by_id[cid] = text
        meta_by_id[cid] = meta
    rank_tables.append(vec_rank)

    # BM25 ranking table.
    bm25_rank: dict[str, int] = {}
    if corpus:
        scores = corpus["bm25"].scores(query)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        for rank, idx in enumerate(ranked[:fetch_k]):
            if scores[idx] <= 0:
                continue
            cid = corpus["ids"][idx]
            bm25_rank[cid] = rank
            text_by_id.setdefault(cid, corpus["documents"][idx])
            meta_by_id.setdefault(cid, corpus["metadatas"][idx] or {})
        rank_tables.append(bm25_rank)

    # Reciprocal Rank Fusion.
    fused: dict[str, float] = defaultdict(float)
    for table in rank_tables:
        for cid, rank in table.items():
            fused[cid] += 1.0 / (_RRF_K + rank)

    if not fused:
        # Nothing fused (e.g. both empty) → return raw vector hits.
        return [{"id": c, "text": t, "meta": m, "rrf_score": 0.0,
                 "vector_rank": vec_rank.get(c), "bm25_rank": None}
                for c, t, m in vector_hits[:n_results]]

    ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:n_results]
    return [
        {
            "id": cid,
            "text": text_by_id.get(cid, ""),
            "meta": meta_by_id.get(cid, {}),
            "rrf_score": round(score, 6),
            "vector_rank": vec_rank.get(cid),
            "bm25_rank": bm25_rank.get(cid),
        }
        for cid, score in ordered
    ]
