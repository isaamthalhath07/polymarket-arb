"""Internal-consistency arbitrage scanner (the closest thing to risk-free on Polymarket).

Two structurally-locked edges:
  1. Single market:  buy YES + buy NO for < $1  -> one side pays $1 at resolution.
     i.e. ask(YES) + ask(NO) < 1  =>  profit = 1 - that sum.
  2. Neg-risk group (exactly one winner across N outcomes):
     a) sum of ask(YES_i) < 1     -> buy one YES of each, one pays $1 -> profit = 1 - sum.
     b) sum of bid(YES_i) > 1     -> sell one YES of each            -> profit = sum - 1.

Caveats baked into output: needs taker fills (fees!), capital locked till resolution,
must be truly mutually-exclusive & exhaustive. We prefer 0-fee (geopolitics) markets.
"""
from __future__ import annotations

import requests
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()
from scanner.pricing import fetch_book

CLOB = "https://clob.polymarket.com"
EDGE_MIN = 0.005  # ignore gaps under half a cent (noise/fees)


def sampling():
    d = requests.get(f"{CLOB}/sampling-markets", timeout=30).json()
    return d.get("data", d) if isinstance(d, dict) else d


def main():
    ms = sampling()
    print(f"[arb] {len(ms)} reward-eligible markets; scanning for locked edges...")

    singles_hits = []
    groups = defaultdict(list)
    for m in ms:
        toks = {(t.get("outcome") or "").lower(): t.get("token_id") for t in (m.get("tokens") or [])}
        yes, no = toks.get("yes"), toks.get("no")
        nrid = m.get("neg_risk_market_id") or ""
        fee = m.get("maker_base_fee", 0)
        if nrid:
            groups[nrid].append((m.get("question", ""), yes, fee))
        # single-market YES+NO test (works for any binary, neg-risk or not)
        if yes and no:
            try:
                by, bn = fetch_book(yes), fetch_book(no)
            except Exception:
                continue
            if by.best_ask and bn.best_ask:
                cost = by.best_ask + bn.best_ask
                if cost < 1 - EDGE_MIN:
                    singles_hits.append((1 - cost, cost, fee, m.get("question", "")[:50]))

    print("\n=== SINGLE-MARKET (buy YES + buy NO < $1) ===")
    if singles_hits:
        singles_hits.sort(key=lambda x: -x[0])
        for edge, cost, fee, q in singles_hits[:20]:
            print(f"  profit/{'$1':>3} = {edge:.3f}  (YES+NO ask = {cost:.3f})  fee={fee}  {q}")
    else:
        print("  none — all binary YES+NO sum to >= ~$1 (efficient).")

    print("\n=== NEG-RISK GROUPS (sum of YES across outcomes) ===")
    hits = 0
    for nrid, members in groups.items():
        if len(members) < 2:
            continue
        ask_sum = 0.0; bid_sum = 0.0; ok = True; fee0 = members[0][2]
        for q, yes, fee in members:
            if not yes:
                ok = False; break
            try:
                b = fetch_book(yes)
            except Exception:
                ok = False; break
            if not b.best_ask or not b.best_bid:
                ok = False; break
            ask_sum += b.best_ask; bid_sum += b.best_bid
        if not ok:
            continue
        title = members[0][0][:40]
        if ask_sum < 1 - EDGE_MIN:
            print(f"  BUY-ALL edge {1-ask_sum:.3f}: sum ask(YES)={ask_sum:.3f} over {len(members)} outcomes  fee={fee0}  [{title}...]")
            hits += 1
        elif bid_sum > 1 + EDGE_MIN:
            print(f"  SELL-ALL edge {bid_sum-1:.3f}: sum bid(YES)={bid_sum:.3f} over {len(members)} outcomes  fee={fee0}  [{title}...]")
            hits += 1
    if hits == 0:
        print("  none — all neg-risk groups sum to ~$1 (efficient).")


if __name__ == "__main__":
    main()
