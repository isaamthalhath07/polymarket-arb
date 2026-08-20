"""Polymarket dependency-driven arbitrage scanner.

Pipeline:
  1. Fetch active markets from Gamma API
  2. Embed titles+descriptions locally, prefilter candidate pairs by cosine similarity
  3. Classify novel pairs with Claude (formal logic prompt, cached)
  4. For dependent pairs, fetch CLOB books and size arbitrage edges
  5. Print ranked actionable arbs

Modes:
  --daemon            run in a loop, refreshing on --interval
  --interval N        seconds between daemon iterations (default 300)
  --max-llm-calls N   abort the run if classifier calls exceed N (default 50)
  --max-events N      cap Gamma event fetch (default unbounded)
  --top-k K           candidate neighbors per market (default 10)
  --min-cosine X      cosine threshold for prefilter (default 0.45)
  --min-edge X        minimum edge_per_unit to print (default 0.01)
  --min-profit X      minimum expected $ profit to print (default 5.0)
  --depth N           order book depth walked per token (default 3)
  --dry-run           skip CLOB fetch and arb sizing, only classify
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from dataclasses import dataclass

from dotenv import load_dotenv
from tabulate import tabulate

from scanner.arb import ArbResult, compute_arb
from scanner.budget import Budget, BudgetExceeded
from scanner.cache import Cache
from scanner.classifier import Classifier
from scanner.markets import fetch_all_normalized_markets
from scanner.prefilter import candidate_pairs, embed_markets
from scanner.pricing import fetch_books
from scanner.reduce import OTHER_SYNTH_ID


@dataclass
class ScannerConfig:
    daemon: bool
    interval: int
    max_llm_calls: int
    max_events: int | None
    top_k: int
    min_cosine: float
    min_edge: float
    min_profit: float
    depth: int
    dry_run: bool
    db_path: str
    min_liquidity: float


def parse_args() -> ScannerConfig:
    p = argparse.ArgumentParser(description="Polymarket dependency arbitrage scanner")
    p.add_argument("--daemon", action="store_true")
    p.add_argument("--interval", type=int, default=300)
    p.add_argument("--max-llm-calls", type=int, default=50)
    p.add_argument("--max-events", type=int, default=None)
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--min-cosine", type=float, default=0.45)
    p.add_argument("--min-edge", type=float, default=0.01)
    p.add_argument("--min-profit", type=float, default=5.0)
    p.add_argument("--depth", type=int, default=3)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--db", default="scanner.db")
    p.add_argument(
        "--min-liquidity",
        type=float,
        default=500.0,
        help="drop markets with CLOB liquidity below this (illiquid = no order book)",
    )
    a = p.parse_args()
    return ScannerConfig(
        daemon=a.daemon,
        interval=a.interval,
        max_llm_calls=a.max_llm_calls,
        max_events=a.max_events,
        top_k=a.top_k,
        min_cosine=a.min_cosine,
        min_edge=a.min_edge,
        min_profit=a.min_profit,
        depth=a.depth,
        dry_run=a.dry_run,
        db_path=a.db,
        min_liquidity=a.min_liquidity,
    )


def _resolve_subset_tokens(market: dict, ids: list[str]) -> list[str]:
    """Map condition_ids from the classifier output back to YES token ids in the original market.

    The prompt input embeds each condition's YES token id as its `id`, so this is direct.
    OTHER_SYNTH is rejected — we never arb through the synthetic catch-all.
    """
    out: list[str] = []
    by_id = {c["id"]: c for c in market["conditions"]}
    for cid in ids:
        if cid == OTHER_SYNTH_ID:
            return []
        if cid not in by_id:
            return []
        out.append(cid)
    return out


def run_once(cfg: ScannerConfig) -> int:
    """Execute one scan cycle. Returns number of arbs printed."""
    cache = Cache(cfg.db_path)
    classifier: Classifier | None = None if cfg.dry_run else None  # lazy
    budget = Budget(max_calls=cfg.max_llm_calls)

    print("[scan] fetching active markets from Gamma...")
    markets = fetch_all_normalized_markets(max_events=cfg.max_events, min_liquidity=cfg.min_liquidity)
    print(f"[scan] {len(markets)} markets normalized")
    for m in markets:
        cache.upsert_market(m)

    print("[scan] embedding markets...")
    embeddings = embed_markets(markets, cache)

    print(f"[scan] computing candidate pairs (top_k={cfg.top_k}, min_cos={cfg.min_cosine})...")
    pairs = candidate_pairs(markets, embeddings, top_k=cfg.top_k, min_cosine=cfg.min_cosine)
    print(f"[scan] {len(pairs)} candidate pairs above similarity threshold")

    by_cid = {m["condition_id"]: m for m in markets}
    classifier = Classifier()
    dependent_results: list[tuple[dict, dict, dict]] = []
    cache_hits = 0
    new_classifications = 0

    for a_cid, b_cid, sim in pairs:
        m1, m2 = by_cid[a_cid], by_cid[b_cid]
        cached = cache.get_classification(a_cid, b_cid, m1["rules_hash"], m2["rules_hash"])
        if cached is not None:
            cache_hits += 1
            result = cached
        else:
            try:
                budget.check()
            except BudgetExceeded as e:
                print(f"[scan] {e} — stopping classification phase")
                break
            try:
                result, cost = classifier.classify(m1, m2)
            except Exception as exc:
                print(f"[scan] classifier error on ({a_cid[:10]}..,{b_cid[:10]}..): {exc}")
                continue
            budget.record(cost)
            new_classifications += 1
            cache.save_classification(a_cid, b_cid, m1["rules_hash"], m2["rules_hash"], result, cost)

        if result.get("step5_self_check", {}).get("abstain"):
            continue
        if result.get("step4_dependence", {}).get("dependent"):
            dependent_results.append((m1, m2, result))

    print(
        f"[scan] classification: {cache_hits} cache hits, {new_classifications} new, "
        f"{len(dependent_results)} dependent pairs. {budget.summary()}"
    )

    if cfg.dry_run:
        print("[scan] dry-run mode — skipping CLOB/arb phase")
        cache.close()
        return 0

    actionable: list[tuple[dict, dict, dict, ArbResult]] = []
    for m1, m2, result in dependent_results:
        subsets = result.get("step4_dependence", {}).get("dependent_subsets", [])
        for subset in subsets:
            s_ids = _resolve_subset_tokens(m1, subset.get("s_m1", []))
            sp_ids = _resolve_subset_tokens(m2, subset.get("s_prime_m2", []))
            if not s_ids or not sp_ids:
                continue
            books_map = fetch_books(s_ids + sp_ids)
            s_books = [books_map[t] for t in s_ids if t in books_map]
            sp_books = [books_map[t] for t in sp_ids if t in books_map]
            if len(s_books) != len(s_ids) or len(sp_books) != len(sp_ids):
                continue
            res = compute_arb(s_books, sp_books, subset.get("relation", ""), depth=cfg.depth)
            if res is None:
                continue
            if res.edge_per_unit < cfg.min_edge or res.expected_profit_usd < cfg.min_profit:
                continue
            actionable.append((m1, m2, subset, res))
            books_snapshot = {tid: b.to_dict() for tid, b in books_map.items()}
            cache.save_arb_snapshot(
                m1["condition_id"],
                m2["condition_id"],
                subset,
                res.edge_per_unit,
                res.max_units,
                res.expected_profit_usd,
                books_snapshot,
            )

    actionable.sort(key=lambda x: -x[3].expected_profit_usd)
    if actionable:
        rows = []
        for m1, m2, subset, res in actionable:
            rows.append(
                [
                    m1["title"][:40],
                    m2["title"][:40],
                    subset.get("relation"),
                    f"{res.edge_per_unit:.4f}",
                    f"{res.max_units:.1f}",
                    f"${res.expected_profit_usd:.2f}",
                ]
            )
        print(tabulate(rows, headers=["M1", "M2", "rel", "edge/u", "units", "profit"]))
    else:
        print("[scan] no actionable arbs above thresholds")
    cache.close()
    return len(actionable)


_running = True


def _handle_sigint(*_) -> None:
    global _running
    _running = False
    print("\n[scan] shutdown requested, finishing current cycle...")


def main() -> int:
    load_dotenv()
    cfg = parse_args()
    signal.signal(signal.SIGINT, _handle_sigint)
    if not cfg.daemon:
        run_once(cfg)
        return 0
    print(f"[scan] daemon mode, interval={cfg.interval}s")
    while _running:
        try:
            run_once(cfg)
        except Exception as exc:
            print(f"[scan] cycle error: {exc}")
        if not _running:
            break
        for _ in range(cfg.interval):
            if not _running:
                break
            time.sleep(1)
    print("[scan] daemon stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
