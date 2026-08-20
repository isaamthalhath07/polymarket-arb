"""PAPER (fake-money) market-making practice.

It auto-picks 2 calm reward-eligible markets, pretends to keep a buy + sell order near
the price, and tracks two things every minute:
  - rewards you'd earn (the daily bonus pool, your estimated share)
  - money lost when the price moves through your stale order (adverse selection)

NO money is used. NO orders are placed. NO API key needed. Just watch the NET number.
If NET is positive after a few days, real market-making is plausible. If negative, it isn't.

Run:  python paper_quote.py
Stop: Ctrl+C  (prints a summary)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import requests
from dotenv import load_dotenv

load_dotenv()
from scanner.pricing import fetch_book

CLOB = "https://clob.polymarket.com"
CAPITAL_PER_MARKET = 200.0   # fake USD per market
QUOTE_INSIDE = 0.5           # how many cents inside max_spread we sit (safety vs pickoff)
POLL_SECONDS = 60            # check once a minute (matches reward sampling)


def get_sampling_markets():
    r = requests.get(f"{CLOB}/sampling-markets", timeout=30)
    r.raise_for_status()
    d = r.json()
    return d.get("data", d) if isinstance(d, dict) else d


def pick_calm_markets(n=2):
    """Calm = decent pool, mid-priced (0.2-0.8), not resolving soon, some spread room."""
    ms = get_sampling_markets()
    scored = []
    for m in ms:
        r = m.get("rewards") or {}
        pool = sum((x.get("rewards_daily_rate", 0) or 0) for x in (r.get("rates") or []))
        if pool < 30:
            continue
        yes = next((t.get("token_id") for t in (m.get("tokens") or [])
                    if (t.get("outcome") or "").lower() == "yes"), None)
        if not yes:
            continue
        try:
            b = fetch_book(yes)
        except Exception:
            continue
        if not b.best_bid or not b.best_ask:
            continue
        mid = (b.best_bid + b.best_ask) / 2
        if mid < 0.2 or mid > 0.8:
            continue  # avoid edges (resolution risk)
        scored.append((pool, m.get("question", "")[:46], yes,
                       float((r.get("max_spread") or 3.0)), float(r.get("min_size") or 0), pool))
    scored.sort(key=lambda x: -x[0])
    return scored[:n]


@dataclass
class Booth:
    question: str
    token: str
    pool: float
    max_spread: float
    min_size: float
    inventory: float = 0.0      # shares of YES held (+long / -short)
    cash: float = 0.0           # fake cash from fills
    rewards: float = 0.0        # fake rewards accrued
    prev_bid: float | None = None
    prev_ask: float | None = None
    fills: int = 0


def step(booth: Booth):
    try:
        b = fetch_book(booth.token)
    except Exception:
        return
    if not b.best_bid or not b.best_ask:
        return
    mid = (b.best_bid + b.best_ask) / 2
    size = (CAPITAL_PER_MARKET / 2) / max(mid, 0.01)
    if size < booth.min_size:
        size = booth.min_size

    # --- did the market move through our PREVIOUS quotes? (adverse-selection fills) ---
    if booth.prev_bid is not None and b.best_ask <= booth.prev_bid:
        booth.inventory += size; booth.cash -= size * booth.prev_bid; booth.fills += 1
    if booth.prev_ask is not None and b.best_bid >= booth.prev_ask:
        booth.inventory -= size; booth.cash += size * booth.prev_ask; booth.fills += 1

    # --- accrue this minute's reward share (very rough model) ---
    off = max(booth.max_spread - QUOTE_INSIDE, 0.5)  # cents from mid
    w = ((booth.max_spread - off / 1.0) / booth.max_spread) ** 2 if off < booth.max_spread else 0
    # assume we capture a modest 10% of the pool when present (optimistic-but-not-crazy)
    booth.rewards += (0.10 * booth.pool) / 1440.0

    # --- re-center quotes for next minute ---
    tick = 0.01
    booth.prev_bid = round(mid - off / 100.0, 2)
    booth.prev_ask = round(mid + off / 100.0, 2)
    return mid


def value(booth: Booth, mid: float) -> float:
    return booth.cash + booth.inventory * mid + booth.rewards


def main():
    print("[paper] picking 2 calm reward markets...")
    picks = pick_calm_markets(2)
    if not picks:
        print("no suitable calm markets right now; try later.")
        return
    booths = [Booth(question=q, token=t, pool=p, max_spread=ms, min_size=mn)
              for (p, q, t, ms, mn, _) in picks]
    for bo in booths:
        print(f"  -> {bo.question}  (pool ${bo.pool}/day, max_spread {bo.max_spread}c)")
    print("\nRunning. Leave it open. Ctrl+C to stop and see results.\n")
    start = time.time()
    try:
        while True:
            for bo in booths:
                mid = step(bo)
            hrs = (time.time() - start) / 3600
            line = f"[{hrs:5.2f}h] "
            for bo in booths:
                try:
                    b = fetch_book(bo.token); mid = (b.best_bid + b.best_ask) / 2
                except Exception:
                    mid = 0.5
                line += f"| {bo.question[:18]}: rew ${bo.rewards:5.2f}  inv {bo.inventory:6.1f}  net ${value(bo, mid):7.2f} (fills {bo.fills}) "
            print(line)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\n\n===== PAPER RESULTS =====")
        total = 0.0
        for bo in booths:
            try:
                b = fetch_book(bo.token); mid = (b.best_bid + b.best_ask) / 2
            except Exception:
                mid = 0.5
            net = value(bo, mid)
            total += net
            print(f"{bo.question}")
            print(f"   rewards earned : ${bo.rewards:7.2f}")
            print(f"   trading P&L    : ${bo.cash + bo.inventory*mid:7.2f}  (inventory {bo.inventory:.1f} shares, {bo.fills} fills)")
            print(f"   NET            : ${net:7.2f}")
        print(f"\nTOTAL NET (fake): ${total:7.2f}")
        print("Positive over several days => real MM is plausible. Negative => don't risk real money.")


if __name__ == "__main__":
    main()
