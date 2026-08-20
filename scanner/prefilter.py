"""Embedding-based candidate pair prefilter using a local sentence-transformers model."""
from __future__ import annotations

import hashlib
from typing import Iterable

import numpy as np

from .cache import Cache

_MODEL = None
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def _get_model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer

        _MODEL = SentenceTransformer(_MODEL_NAME)
    return _MODEL


def _title_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _embed_text(market: dict) -> str:
    return f"{market['title']}. {market.get('description', '')[:200]}"


def embed_markets(markets: list[dict], cache: Cache) -> dict[str, np.ndarray]:
    """Embed each market, reusing cached vectors when title+description haven't changed."""
    out: dict[str, np.ndarray] = {}
    to_compute: list[tuple[str, str]] = []
    hashes: dict[str, str] = {}
    for m in markets:
        text = _embed_text(m)
        h = _title_hash(text)
        hashes[m["condition_id"]] = h
        cached = cache.get_embedding(m["condition_id"], h)
        if cached is not None:
            out[m["condition_id"]] = cached
        else:
            to_compute.append((m["condition_id"], text))
    if to_compute:
        model = _get_model()
        texts = [t for _, t in to_compute]
        vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        for (cid, _), vec in zip(to_compute, vectors):
            v = np.asarray(vec, dtype=np.float32)
            cache.save_embedding(cid, v, hashes[cid])
            out[cid] = v
    return out


def candidate_pairs(
    markets: list[dict],
    embeddings: dict[str, np.ndarray],
    *,
    top_k: int = 10,
    min_cosine: float = 0.45,
) -> list[tuple[str, str, float]]:
    """Return deduplicated unordered pairs (a_cid, b_cid, similarity) sorted desc by similarity.

    For each market, find top-K nearest neighbors above the cosine threshold and emit pairs.
    Vectors are L2-normalized so dot product == cosine similarity.
    """
    cids = [m["condition_id"] for m in markets if m["condition_id"] in embeddings]
    if len(cids) < 2:
        return []
    matrix = np.stack([embeddings[c] for c in cids])
    sims = matrix @ matrix.T
    np.fill_diagonal(sims, -1.0)
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str, float]] = []
    n = len(cids)
    k = min(top_k, n - 1)
    for i in range(n):
        idx = np.argpartition(-sims[i], k)[:k]
        for j in idx:
            s = float(sims[i, j])
            if s < min_cosine:
                continue
            a, b = sorted((cids[i], cids[int(j)]))
            if (a, b) in seen:
                continue
            seen.add((a, b))
            pairs.append((a, b, s))
    pairs.sort(key=lambda p: -p[2])
    return pairs
