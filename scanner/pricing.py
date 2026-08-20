"""CLOB order book fetcher.

Wraps py_clob_client to expose order books for a list of YES token ids. Read-only; no
private key needed for `get_order_book`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

@dataclass
class Book:
    token_id: str
    bids: list[tuple[float, float]] = field(default_factory=list)  # (price, size) sorted desc
    asks: list[tuple[float, float]] = field(default_factory=list)  # (price, size) sorted asc

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2

    def to_dict(self) -> dict:
        return {
            "token_id": self.token_id,
            "bids": self.bids,
            "asks": self.asks,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "mid": self.mid,
        }


@lru_cache(maxsize=1)
def _client():
    from py_clob_client.client import ClobClient

    host = os.environ.get("CLOB_API", "https://clob.polymarket.com")
    return ClobClient(host, chain_id=137)


def _is_empty_book_error(exc: Exception) -> bool:
    """Polymarket's /book returns 404 'No orderbook exists' when a token has zero resting
    orders. That's an empty book, not a failure."""
    msg = str(exc).lower()
    return "no orderbook exists" in msg or "404" in msg


def fetch_book(token_id: str) -> Book:
    raw = _client().get_order_book(token_id)
    bids_raw = getattr(raw, "bids", None) or []
    asks_raw = getattr(raw, "asks", None) or []
    bids = sorted(
        ((float(b.price), float(b.size)) for b in bids_raw),
        key=lambda x: -x[0],
    )
    asks = sorted(
        ((float(a.price), float(a.size)) for a in asks_raw),
        key=lambda x: x[0],
    )
    return Book(token_id=token_id, bids=bids, asks=asks)


def fetch_books(token_ids: Iterable[str]) -> dict[str, Book]:
    out: dict[str, Book] = {}
    for tid in token_ids:
        try:
            out[tid] = fetch_book(tid)
        except Exception as exc:
            if _is_empty_book_error(exc):
                # Illiquid token: no resting orders. Return an empty book (no arb possible).
                out[tid] = Book(token_id=tid, bids=[], asks=[])
            else:
                print(f"[pricing] failed to fetch {tid}: {exc}")
    return out


def fetch_books_batch(token_ids: list[str], chunk: int = 200) -> dict[str, Book]:
    """Fast multi-book fetch via the CLOB POST /books endpoint (many books per request).
    Far faster than per-token GET /book when polling hundreds of tokens (e.g. a monitor)."""
    import requests
    host = os.environ.get("CLOB_API", "https://clob.polymarket.com")
    out: dict[str, Book] = {}
    for i in range(0, len(token_ids), chunk):
        batch = token_ids[i:i + chunk]
        try:
            r = requests.post(f"{host}/books", json=[{"token_id": t} for t in batch], timeout=30)
            if r.status_code != 200:
                continue
            for b in r.json():
                aid = str(b.get("asset_id"))
                bids = sorted(((float(x["price"]), float(x["size"])) for x in (b.get("bids") or [])), key=lambda x: -x[0])
                asks = sorted(((float(x["price"]), float(x["size"])) for x in (b.get("asks") or [])), key=lambda x: x[0])
                out[aid] = Book(token_id=aid, bids=bids, asks=asks)
        except Exception as exc:
            print(f"[pricing] batch fetch error: {exc}")
    return out
