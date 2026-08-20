"""Polymarket liquidity-rewards scout.

Ranks reward-eligible markets by ESTIMATED net yield on your capital, with every known
failure mode encoded as a risk penalty. This does NOT place orders — it tells you where
to deploy and what to expect. Pure HTTP + arithmetic; no LLM, runs free.

Reward model (from Polymarket docs):
  S(v, s) = ((v - s)/v)^2          # score weight of one resting order
    v = market max_spread (cents); s = order's distance from midpoint (cents)
  Two-sided combine: Qmkt = max(min(Qbid, Qask), max(Qbid, Qask)/c),  c = 3.0
  Outside price band [0.10, 0.90], liquidity MUST be two-sided to score.
  Daily reward to you ~= (your_Q / total_Q) * daily_pool_usdc
  Min payout $1/day or you get nothing.

All yield numbers are GROSS of adverse selection (the real P&L killer) and approximate;
they exist to RANK markets, not to promise returns.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
from scanner.pricing import fetch_book  # reuse working CLOB book fetch

CLOB = "https://clob.polymarket.com"
C_BOOST = 3.0


def get_sampling_markets() -> list[dict]:
    r = requests.get(f"{CLOB}/sampling-markets", timeout=30)
    r.raise_for_status()
    d = r.json()
    return d.get("data", d) if isinstance(d, dict) else d


def s_weight(v_cents: float, s_cents: float) -> float:
    """Quadratic score weight; 0 outside the band."""
    if s_cents >= v_cents:
        return 0.0
    return ((v_cents - s_cents) / v_cents) ** 2


def book_qualifying_score(levels, mid, v_cents, side_sign):
    """Sum S(v,s)*size for resting orders within the reward band on one side.
    levels: list of (price, size); side_sign: -1 for bids (below mid), +1 for asks."""
    q = 0.0
    depth = 0.0
    for price, size in levels:
        s = abs(price - mid) * 100.0
        w = s_weight(v_cents, s)
        if w > 0:
            q += w * size
            depth += size
    return q, depth


@dataclass
class Opp:
    question: str
    pool: float
    max_spread: float
    min_size: float
    maker_fee: int
    mid: float
    spread_c: float
    days_left: float
    my_size: float
    my_daily_reward: float
    yield_pct_day: float
    risk_score: float
    flags: str


def days_until(iso: str | None) -> float:
    if not iso:
        return 999.0
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return max((dt - datetime.now(timezone.utc)).total_seconds() / 86400.0, 0.0)
    except Exception:
        return 999.0


def analyze(m: dict, capital: float) -> Opp | None:
    r = m.get("rewards") or {}
    pool = sum((x.get("rewards_daily_rate", 0) or 0) for x in (r.get("rates") or []))
    if pool <= 0:
        return None
    v = float(r.get("max_spread") or 3.0)
    min_size = float(r.get("min_size") or 0)
    yes = next((t.get("token_id") for t in (m.get("tokens") or [])
                if (t.get("outcome") or "").lower() == "yes"), None)
    if not yes:
        return None
    try:
        b = fetch_book(yes)
    except Exception:
        return None
    if not b.best_bid or not b.best_ask:
        return None  # empty book: can't model competition
    mid = (b.best_bid + b.best_ask) / 2.0
    spread_c = (b.best_ask - b.best_bid) * 100.0

    # existing competition within the reward band
    qb, _ = book_qualifying_score(b.bids, mid, v, -1)
    qa, _ = book_qualifying_score(b.asks, mid, v, +1)
    q_existing = max(min(qb, qa), max(qb, qa) / C_BOOST)

    # MY plan: two-sided, join the touch (s = half the current spread, min 0.5 tick)
    s_you = max(spread_c / 2.0, 0.5)
    if s_you >= v:
        return None  # natural spread already outside band; nothing to earn cleanly
    # size per side from capital (capital split across the two legs), respect min_size
    per_side_usd = capital / 2.0
    size_shares = per_side_usd / max(mid, 0.01)
    if size_shares < min_size:
        size_shares = min_size  # must meet min to qualify (forces more capital)
    w = s_weight(v, s_you)
    my_q = w * size_shares  # symmetric two-sided => Qmin = this
    total_q = q_existing + my_q
    share = my_q / total_q if total_q > 0 else 0.0
    my_daily = share * pool
    capital_used = size_shares * mid * 2.0
    yld = (my_daily / capital_used * 100.0) if capital_used > 0 else 0.0

    days = days_until(m.get("end_date_iso"))

    # ---- risk flags & penalty ----
    flags = []
    risk = 1.0
    if mid < 0.10 or mid > 0.90:
        flags.append("EDGE(0/1):resolution+forced2sided")
        risk *= 0.4
    if days < 7:
        flags.append(f"RESOLVES~{days:.0f}d")
        risk *= 0.5
    if spread_c <= 1.0:
        flags.append("CROWDED(1tick)")
        risk *= 0.6
    if my_daily < 1.0:
        flags.append("BELOW$1MIN")
        risk *= 0.2
    cat_fee = m.get("maker_base_fee", 0)
    # high counterparty/news categories => more adverse selection
    if pool >= 200:
        flags.append("HIGHPOOL")
    if capital_used > capital * 1.5:
        flags.append(f"NEEDS${capital_used:.0f}")

    risk_adj_yield = yld * risk
    return Opp(
        question=m.get("question", "")[:42],
        pool=pool, max_spread=v, min_size=min_size, maker_fee=cat_fee,
        mid=mid, spread_c=spread_c, days_left=days,
        my_size=size_shares, my_daily_reward=my_daily, yield_pct_day=yld,
        risk_score=risk_adj_yield, flags=",".join(flags) or "-",
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=500.0, help="USD to deploy per market")
    ap.add_argument("--min-pool", type=float, default=20.0, help="min daily USDC pool to consider")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()

    print(f"[scout] fetching reward-eligible markets...")
    ms = get_sampling_markets()
    cands = [m for m in ms
             if sum((x.get("rewards_daily_rate", 0) or 0) for x in ((m.get("rewards") or {}).get("rates") or [])) >= a.min_pool]
    print(f"[scout] {len(cands)} markets with pool >= ${a.min_pool}/day; analyzing books...")

    opps = []
    for m in cands:
        o = analyze(m, a.capital)
        if o:
            opps.append(o)
    opps.sort(key=lambda o: -o.risk_score)

    print(f"\nCapital modeled per market: ${a.capital:.0f}.  Yields are GROSS of adverse selection.\n")
    hdr = "{:>5} {:>5} {:>5} {:>5} {:>5} {:>5} {:>8} {:>8}  {}".format(
        "pool", "mid", "spr", "mxsp", "APR%", "$/day", "radjAPR", "resolve", "market | flags")
    print(hdr); print("-" * 130)
    for o in opps[:a.top]:
        apr = o.yield_pct_day * 365
        radj_apr = o.risk_score * 365
        print("{:>5.0f} {:>5.2f} {:>5.1f} {:>5.1f} {:>5.0f} {:>8.2f} {:>8.0f} {:>6.0f}d  {} | {}".format(
            o.pool, o.mid, o.spread_c, o.max_spread, apr, o.my_daily_reward, radj_apr, o.days_left,
            o.question, o.flags))


if __name__ == "__main__":
    main()
