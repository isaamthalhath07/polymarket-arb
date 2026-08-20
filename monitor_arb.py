"""Continuous arb monitor - 100% local, NO LLM, no classification, no cache.

Catches arbs that flash for seconds when a big order hits. Pure HTTP + arithmetic:
  - rebuilds market GROUPINGS every --refresh seconds (structure changes slowly)
  - re-polls only the order BOOKS every --interval seconds (prices change fast)
  - alerts ONLY on depth-checked, fee-netted, complete-set violations
Conservative worst-case fee (1.5%) so any alert is real across every category.

Run:  python monitor_arb.py            (every 60s)
      python monitor_arb.py --interval 30 --min-liquidity 100
Stop: Ctrl+C
"""
import time, argparse, sys
from collections import defaultdict
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
from scanner.markets import fetch_all_normalized_markets
from scanner.pricing import fetch_books_batch
from xmarket_arb import parse_date

FEE = 0.015          # worst-case taker (economics); geopolitics is 0 -> alerts are conservative
MIN_DEPTH = 20       # shares at the touch on every leg
EDGE_MIN = 0.005     # net edge (after fees) required to alert


def build_groups(max_events, min_liq):
    markets = fetch_all_normalized_markets(max_events=max_events, min_liquidity=min_liq)
    date_groups = defaultdict(list)
    negs = []
    for m in markets:
        if m.get("neg_risk") and len(m["conditions"]) >= 2:
            negs.append(m)
        elif not m.get("neg_risk") and len(m["conditions"]) == 1:
            d = parse_date(m["title"])
            if d:
                date_groups[d[0]].append((d[1], m))
    date_groups = {k: sorted(v, key=lambda x: x[0]) for k, v in date_groups.items() if len(v) >= 2}
    tokens = set()
    for v in date_groups.values():
        for _, m in v: tokens.add(m["conditions"][0]["id"])
    for m in negs:
        for c in m["conditions"]: tokens.add(c["id"])
    return date_groups, negs, list(tokens)


def check_date(members, books):
    alerts = []
    pts = []
    for val, m in members:
        b = books.get(m["conditions"][0]["id"])
        if b and b.best_bid is not None and b.best_ask is not None:
            pts.append((b, m["title"]))
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):  # i = earlier, j = later
            be, ti = pts[i]; bl, tj = pts[j]
            # need P(earlier) <= P(later); arb if bid(earlier) > ask(later)
            edge = be.best_bid - bl.best_ask
            fee = FEE * (be.best_bid * (1 - be.best_bid) + bl.best_ask * (1 - bl.best_ask))
            net = edge - fee
            depth = min(be.bids[0][1] if be.bids else 0, bl.asks[0][1] if bl.asks else 0)
            if net > EDGE_MIN and depth >= MIN_DEPTH:
                alerts.append(f"DATE net {net:.3f} d{depth:.0f}: SELL '{ti[:30]}'@{be.best_bid:.2f} BUY '{tj[:30]}'@{bl.best_ask:.2f}")
    return alerts


def check_neg(m, books):
    legs = []
    for c in m["conditions"]:
        b = books.get(c["id"])
        if not b or b.best_bid is None or b.best_ask is None:
            return []
        legs.append(b)
    sum_ask = sum(b.best_ask for b in legs)
    sum_bid = sum(b.best_bid for b in legs)
    sum_mid = sum(b.mid for b in legs)
    if not (0.90 <= sum_mid <= 1.10):
        return []  # not a clean complete partition
    out = []
    if sum_ask < 1:
        fee = sum(FEE * b.best_ask * (1 - b.best_ask) for b in legs)
        net = (1 - sum_ask) - fee
        depth = min(b.asks[0][1] if b.asks else 0 for b in legs)
        if net > EDGE_MIN and depth >= MIN_DEPTH:
            out.append(f"NEG buy-all net {net:.3f} d{depth:.0f}: {m['title'][:38]}")
    if sum_bid > 1:
        fee = sum(FEE * b.best_bid * (1 - b.best_bid) for b in legs)
        net = (sum_bid - 1) - fee
        depth = min(b.bids[0][1] if b.bids else 0 for b in legs)
        if net > EDGE_MIN and depth >= MIN_DEPTH:
            out.append(f"NEG sell-all net {net:.3f} d{depth:.0f}: {m['title'][:38]}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60, help="seconds between book polls")
    ap.add_argument("--refresh", type=int, default=900, help="seconds between structure rebuilds")
    ap.add_argument("--min-liquidity", type=float, default=200.0)
    ap.add_argument("--max-events", type=int, default=300)
    a = ap.parse_args()

    print(f"[monitor] local, no-LLM arb monitor. interval={a.interval}s fee={FEE*100:.1f}% Ctrl+C to stop.")
    date_groups = negs = tokens = None
    last_refresh = 0
    cycles = 0
    try:
        while True:
            now = time.time()
            if now - last_refresh > a.refresh or date_groups is None:
                print("[monitor] rebuilding market groups...")
                date_groups, negs, tokens = build_groups(a.max_events, a.min_liquidity)
                last_refresh = now
                print(f"[monitor] {len(date_groups)} date-nests, {len(negs)} neg-risk groups, {len(tokens)} tokens")
            books = fetch_books_batch(tokens)
            alerts = []
            for members in date_groups.values():
                alerts += check_date(members, books)
            for m in negs:
                alerts += check_neg(m, books)
            ts = datetime.now().strftime("%H:%M:%S")
            if alerts:
                print(f"\n[{ts}]  *** {len(alerts)} ARB ALERT(S) ***")
                for al in alerts:
                    print("   " + al)
                print()
            else:
                cycles += 1
                print(f"[{ts}] clean (cycle {cycles}) - no arb > {EDGE_MIN*100:.1f}% net", flush=True)
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print("\n[monitor] stopped.")


if __name__ == "__main__":
    main()
