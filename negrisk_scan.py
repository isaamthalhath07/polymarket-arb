"""Neg-risk group arbitrage scan (done RIGHT this time).

For a complete mutually-exclusive-exhaustive set (exactly one outcome wins):
  BUY-ALL  arb: sum(ask_YES) < 1   -> buy 1 YES of each, one pays $1, profit = 1 - sum
  SELL-ALL arb: sum(bid_YES) > 1   -> sell 1 YES of each, profit = sum - 1

Guards against the false positives from last time:
  - COMPLETENESS: only trust groups whose sum(mid) is ~1.0 (a real partition). Waymo-style
    overlapping/incomplete sets have sum(mid) far from 1 -> skipped.
  - DEPTH: require real shares at the touch (not dust) on every leg.
  - FEES: net the edge against taker fees per leg.
"""
from dotenv import load_dotenv
load_dotenv()
from scanner.markets import fetch_all_normalized_markets
from scanner.pricing import fetch_books

FEE_RATE = 0.0125          # conservative taker (~culture); many cats lower, geo=0
MIN_DEPTH = 20             # shares required at the touch on each leg to count as executable
EDGE_MIN = 0.01            # ignore sub-1c gaps


def main():
    print("[nr] fetching neg-risk markets (full outcome sets)...")
    markets = fetch_all_normalized_markets(max_events=400, min_liquidity=200)
    negs = [m for m in markets if m.get("neg_risk") and len(m["conditions"]) >= 2]
    print(f"[nr] {len(negs)} neg-risk groups\n")

    hits = 0
    for m in negs:
        toks = [c["id"] for c in m["conditions"]]
        books = fetch_books(toks)
        legs = []
        ok = True
        for c in m["conditions"]:
            b = books.get(c["id"])
            if not b or b.best_bid is None or b.best_ask is None:
                ok = False; break
            ask_depth = b.asks[0][1] if b.asks else 0
            bid_depth = b.bids[0][1] if b.bids else 0
            legs.append((b.best_bid, b.best_ask, b.mid, ask_depth, bid_depth))
        if not ok or len(legs) < 2:
            continue
        sum_ask = sum(l[1] for l in legs)
        sum_bid = sum(l[0] for l in legs)
        sum_mid = sum(l[2] for l in legs)
        # COMPLETENESS: a true partition sums to ~1 at mid
        if not (0.90 <= sum_mid <= 1.10):
            continue
        title = m["title"][:46].encode("ascii", "replace").decode()
        # BUY-ALL
        if sum_ask < 1 - EDGE_MIN:
            min_ask_depth = min(l[3] for l in legs)
            fees = sum(FEE_RATE * l[1] * (1 - l[1]) for l in legs)
            net = (1 - sum_ask) - fees
            depth_ok = min_ask_depth >= MIN_DEPTH
            if net > 0:
                hits += 1
                print(f"  BUY-ALL  edge {net:.3f} (sum_ask={sum_ask:.3f}, mid={sum_mid:.2f}) "
                      f"min_depth={min_ask_depth:.0f}sh {'EXECUTABLE' if depth_ok else 'DUST'}  [{title}]")
        # SELL-ALL
        if sum_bid > 1 + EDGE_MIN:
            min_bid_depth = min(l[4] for l in legs)
            fees = sum(FEE_RATE * l[0] * (1 - l[0]) for l in legs)
            net = (sum_bid - 1) - fees
            depth_ok = min_bid_depth >= MIN_DEPTH
            if net > 0:
                hits += 1
                print(f"  SELL-ALL edge {net:.3f} (sum_bid={sum_bid:.3f}, mid={sum_mid:.2f}) "
                      f"min_depth={min_bid_depth:.0f}sh {'EXECUTABLE' if depth_ok else 'DUST'}  [{title}]")
    print(f"\n[nr] groups with net-positive arb: {hits}")
    if hits == 0:
        print("[nr] no executable neg-risk arb (complete sets are consistent after fees).")


if __name__ == "__main__":
    main()
