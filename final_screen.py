"""Rank reward markets by backtested profitability. Calm markets = profit; volatile = loss.
Conservative 5% reward-share assumption. Shows 30-day price movement (volatility) too."""
import time, requests
from dotenv import load_dotenv
load_dotenv()

CLOB = "https://clob.polymarket.com"
CAPITAL = 200.0
SHARE = 0.05  # conservative: assume we capture only 5% of the pool


def sampling():
    d = requests.get(f"{CLOB}/sampling-markets", timeout=30).json()
    return d.get("data", d) if isinstance(d, dict) else d


def hist(token):
    r = requests.get(f"{CLOB}/prices-history",
                     params={"market": token, "interval": "1m", "fidelity": 60}, timeout=30)
    if r.status_code != 200:
        return []
    return [(p["t"], float(p["p"])) for p in r.json().get("history", [])]


def bt(h, pool, mxsp, mnsz):
    off = max(mxsp - 0.5, 0.5) / 100.0
    cash = inv = rew = 0.0; fills = 0; move = 0.0
    for i in range(len(h) - 1):
        t0, p0 = h[i]; t1, p1 = h[i + 1]
        move += abs(p1 - p0)
        size = max((CAPITAL / 2) / max(p0, 0.01), mnsz)
        if p1 <= p0 - off:
            inv += size; cash -= size * (p0 - off); fills += 1
        elif p1 >= p0 + off:
            inv -= size; cash += size * (p0 + off); fills += 1
        rew += pool * SHARE * max((t1 - t0) / 86400.0, 0)
    last = h[-1][1]
    trade = cash + inv * last
    return dict(rew=rew, trade=trade, net=trade + rew, fills=fills, move=move * 100)


def main():
    ms = sampling()
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

    rows = []
    for pool, q, yes, mxsp, mnsz in cands[:25]:
        h = hist(yes)
        if len(h) < 10:
            continue
        res = bt(h, pool, mxsp, mnsz)
        rows.append((res["net"], pool, q, res["rew"], res["trade"], res["fills"], res["move"]))
    rows.sort(key=lambda x: -x[0])

    print(f"30-day backtest, ${CAPITAL:.0f}/market, CONSERVATIVE {SHARE*100:.0f}% reward share, pessimistic fills\n")
    print("{:>8} {:>5} {:>8} {:>8} {:>5} {:>7}  {}".format(
        "NET", "pool", "rewards", "tradePnL", "fills", "moved¢", "market"))
    print("-" * 92)
    for net, pool, q, rew, trade, fills, move in rows:
        flag = "  <-- WINNER" if net > 0 and trade > -rew * 0.5 else ("  <-- LOSER" if net < 0 else "")
        print("{:>8.0f} {:>5.0f} {:>8.0f} {:>8.0f} {:>5} {:>7.0f}  {}{}".format(
            net, pool, rew, trade, fills, move, q, flag))


if __name__ == "__main__":
    main()
