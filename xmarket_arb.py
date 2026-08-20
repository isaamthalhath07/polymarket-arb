"""Cross-market arbitrage scan over SIMILAR markets whose prices must be ordered.

Two structural relationships (pure logic, not prediction):
  DATE NEST:  "X by <earlier date>" implies "X by <later date>"  -> P(earlier) <= P(later)
  $ LADDER:   "FDV/price above $HIGH"  implies "above $LOW"      -> P(high) <= P(low)
A violation (bid of the stricter leg > ask of the looser leg) = risk-free arb:
  sell the overpriced strict leg, buy the cheap loose leg; it pays off in every outcome.

Auto-groups liquid single-condition markets by a title 'stem', orders by date/threshold,
fetches live books, and flags any price-ordering violation beyond fees.
"""
import re
from collections import defaultdict
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
from scanner.markets import fetch_all_normalized_markets
from scanner.pricing import fetch_books

MONTHS = {m: i for i, m in enumerate(
    ["january","february","march","april","may","june","july","august",
     "september","october","november","december"], 1)}
MUL = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12,
       "thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}


def parse_date(title):
    t = title.lower()
    m = re.search(r"by (?:end of )?(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:,?\s*(\d{4}))?", t)
    if m:
        mo, day, yr = MONTHS[m.group(1)], int(m.group(2)), int(m.group(3) or 2026)
        stem = (t[:m.start()] + t[m.end():]).strip()
        return stem, datetime(yr, mo, day).timestamp()
    m = re.search(r"by (?:end of |december 31,? )?(\d{4})", t) or re.search(r"before (\d{4})", t)
    if m:
        yr = int(m.group(1)); stem = (t[:m.start()] + t[m.end():]).strip()
        return stem, datetime(yr, 12, 31).timestamp()
    return None


def parse_threshold(title):
    t = title.lower()
    # SKIP range-bucket / partition markets (e.g. "38.0-38.4", "<37.5", "between X and Y").
    # Those are disjoint outcomes, NOT a nested ladder -> belongs to the neg-risk sum check.
    if re.search(r"\d+(?:\.\d+)?\s*[-–]\s*\d+", t) or "between" in t or re.search(r"<\s*\d", t):
        return None
    # "above $100m", "$500m", "hit $150k", "above 55", "65+"
    m = re.search(r"(?:above|over|hit|reach|exceed|more than)\s*\$?\s*([\d.]+)\s*(k|m|b|t|thousand|million|billion|trillion)?", t)
    if not m:
        m = re.search(r"\$\s*([\d.]+)\s*(k|m|b|t|thousand|million|billion|trillion)?", t)
    if not m:
        m = re.search(r"\b(\d{2,3})\+", t)  # RT style "65+"
    if m:
        val = float(m.group(1)) * (MUL.get((m.group(2) or "").strip(), 1) if m.lastindex and m.lastindex >= 2 else 1)
        stem = (t[:m.start()] + t[m.end():]).strip()
        stem = re.sub(r"\s+", " ", stem)
        return stem, val
    return None


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-liquidity", type=float, default=500.0)
    ap.add_argument("--max-events", type=int, default=250)
    a = ap.parse_args()
    print(f"[xarb] fetching markets (min_liq=${a.min_liquidity:.0f})...")
    markets = fetch_all_normalized_markets(max_events=a.max_events, min_liquidity=a.min_liquidity)
    singles = [m for m in markets if not m.get("neg_risk") and len(m["conditions"]) == 1]
    print(f"[xarb] {len(singles)} single-condition liquid markets")

    date_groups = defaultdict(list); thr_groups = defaultdict(list)
    for m in singles:
        d = parse_date(m["title"])
        if d:
            date_groups[d[0]].append((d[1], m)); continue
        th = parse_threshold(m["title"])
        if th:
            thr_groups[th[0]].append((th[1], m))

    def scan(groups, kind):
        hits = 0
        for stem, members in groups.items():
            if len(members) < 2: continue
            members.sort(key=lambda x: x[0])
            toks = [m["conditions"][0]["id"] for _, m in members]
            books = fetch_books(toks)
            pts = []
            for val, m in members:
                b = books.get(m["conditions"][0]["id"])
                if not b or b.best_bid is None or b.best_ask is None: continue
                pts.append((val, b.best_bid, b.best_ask, m["title"]))
            if len(pts) < 2: continue
            # DATE: P increases with val -> for i<j (earlier,later) need bid_i <= ask_j; arb if bid_i>ask_j
            # $LADDER: P decreases with val -> for i<j (low,high) need bid_j <= ask_i; arb if bid_j>ask_i
            viol = []
            for i in range(len(pts)):
                for j in range(i+1, len(pts)):
                    vi, bi, ai, ti = pts[i]; vj, bj, aj, tj = pts[j]
                    if kind == "date":
                        if bi - aj > 0.005:
                            viol.append((bi-aj, ti, bi, tj, aj))
                    else:  # threshold: higher val (j) must be <= lower val (i)
                        if bj - ai > 0.005:
                            viol.append((bj-ai, tj, bj, ti, ai))
            if viol:
                hits += 1
                print(f"\n  !! VIOLATION ({kind}) in group: {stem[:50]}")
                for edge, t_sell, bsell, t_buy, abuy in sorted(viol, reverse=True)[:3]:
                    print(f"     edge {edge:.3f}: SELL '{t_sell[:34]}'@bid {bsell:.2f}  BUY '{t_buy[:34]}'@ask {abuy:.2f}")
        return hits

    print(f"\n=== DATE NESTS ({sum(1 for g in date_groups.values() if len(g)>=2)} groups) ===")
    h1 = scan(date_groups, "date")
    print(f"\n=== $ THRESHOLD LADDERS ({sum(1 for g in thr_groups.values() if len(g)>=2)} groups) ===")
    h2 = scan(thr_groups, "thr")
    print(f"\n[xarb] total groups with arb violations: {h1+h2}")
    if h1+h2 == 0:
        print("[xarb] no risk-free ordering violations found (markets internally consistent).")


if __name__ == "__main__":
    main()
