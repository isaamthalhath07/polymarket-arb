"""Backtest the liquidity-rewards market-making strategy on REAL price history.

For each calm reward market: replay ~30 days, posting a bid+ask near the price each step.
- Reward accrual: pool * share * (dt/day)   [share is uncertain -> tested at 5/10/20%]
- Adverse selection: if price moves THROUGH a stale quote, we get filled and mark to new
  price (a loss when trending). This OVER-states pickoffs (no cancel-on-move), so it's a
  PESSIMISTIC floor. If net is positive here, real MM is plausible.

No money, no orders, no key. Pure history replay.
"""
from __future__ import annotations

import time
import requests
from dotenv import load_dotenv

load_dotenv()

CLOB = "https://clob.polymarket.com"
CAPITAL = 200.0          # fake USD per market
DAYS = 30
SHARES_TESTED = [0.05, 0.10, 0.20]   # assumed fraction of the reward pool we capture


def sampling_markets():
    d = requests.get(f"{CLOB}/sampling-markets", timeout=30).json()
    return d.get("data", d) if isinstance(d, dict) else d


def history(token, days=DAYS):
    # interval='1m' (one month) at hourly fidelity returns ~720 points; startTs/endTs 400s.
    r = requests.get(f"{CLOB}/prices-history",
                     params={"market": token, "interval": "1m", "fidelity": 60},
                     timeout=30)
    if r.status_code != 200:
        return []
    h = r.json().get("history", [])
    return [(p["t"], float(p["p"])) for p in h]


def backtest(hist, pool, max_spread, min_size, share):
    if len(hist) < 5:
        return None
    off = max(max_spread - 0.5, 0.5) / 100.0   # dollars from mid
    cash = 0.0; inv = 0.0; rewards = 0.0; fills = 0
    for i in range(len(hist) - 1):
        t0, p0 = hist[i]; t1, p1 = hist[i + 1]
        size = max((CAPITAL / 2) / max(p0, 0.01), min_size)
        bid = p0 - off; ask = p0 + off
        # pickoff fills when price moves through a stale quote
        if p1 <= bid:
            inv += size; cash -= size * bid; fills += 1
        elif p1 >= ask:
            inv -= size; cash += size * ask; fills += 1
        # reward for the elapsed time
        dt_days = max((t1 - t0) / 86400.0, 0)
        rewards += pool * share * dt_days
    last = hist[-1][1]
    trading = cash + inv * last
    net = trading + rewards
    return dict(rewards=rewards, trading=trading, inv=inv, fills=fills, net=net, last=last)


def main():
    ms = sampling_markets()
    cands = []
    for m in ms:
        r = m.get("rewards") or {}
        pool = sum((x.get("rewards_daily_rate", 0) or 0) for x in (r.get("rates") or []))
        if pool < 50:
            continue
        yes = next((t.get("token_id") for t in (m.get("tokens") or [])
                    if (t.get("outcome") or "").lower() == "yes"), None)
        if yes:
            cands.append((pool, m.get("question", "")[:40], yes,
                          float(r.get("max_spread") or 3.0), float(r.get("min_size") or 0)))
    cands.sort(key=lambda x: -x[0])
    cands = cands[:8]

    print(f"Backtest: {DAYS} days, ${CAPITAL:.0f}/market, pessimistic fills (no cancel-on-move)\n")
    print("{:>5} {:>6} {:>22} {:>9} {:>9} {:>9} {:>7}".format(
        "pool", "share", "market", "rewards", "tradePnL", "NET", "fills"))
    print("-" * 78)
    for pool, q, yes, mxsp, mnsz in cands:
        h = history(yes)
        if len(h) < 5:
            print(f"{pool:>5.0f}    ---  {q:>22}  (no history)")
            continue
        for sh in SHARES_TESTED:
            res = backtest(h, pool, mxsp, mnsz, sh)
            if res:
                tag = q if sh == SHARES_TESTED[0] else ""
                print("{:>5.0f} {:>5.0f}% {:>22} {:>9.2f} {:>9.2f} {:>9.2f} {:>7}".format(
                    pool, sh * 100, tag[:22], res["rewards"], res["trading"], res["net"], res["fills"]))
        print()


if __name__ == "__main__":
    main()
