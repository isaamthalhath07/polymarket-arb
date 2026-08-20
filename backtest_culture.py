"""RESEARCH-ONLY backtest of the 'published-metric lag' edge on resolved culture markets.
Pure historical data analysis - no trading, no orders, no platform access.

Test: for RESOLVED box office / Rotten Tomatoes / Netflix markets, take the WINNING outcome
and look at its price trajectory. The info-edge claim is: 'the ground truth is knowable
early, but the market lags.' If true, the winning outcome should still be CHEAP at T-48h /
T-24h before settlement (you could buy it knowing the answer). If the market is efficient,
the winner is already ~1.0 well before settlement -> no edge.

Reports, per market: winner's price 48h/24h before end, and when it first crossed 0.80.
"""
import requests, json
from datetime import datetime, timezone

GAMMA = "https://gamma-api.polymarket.com"; CLOB = "https://clob.polymarket.com"


def closed_events(query):
    d = requests.get(GAMMA+"/public-search", params={"q": query, "limit_per_type": 30}, timeout=20).json()
    return [e for e in d.get("events", []) if e.get("closed")]


def full(slug):
    try: return requests.get(GAMMA+"/events", params={"slug": slug}, timeout=20).json()[0]
    except Exception: return None


def hist(token):
    r = requests.get(CLOB+"/prices-history", params={"market": token, "interval": "max", "fidelity": 60}, timeout=30)
    return [(p["t"], float(p["p"])) for p in r.json().get("history", [])] if r.status_code == 200 else []


def price_at(h, ts):
    """price at-or-before timestamp ts."""
    best = None
    for t, p in h:
        if t <= ts: best = p
        else: break
    return best


def analyze_market(m, end_ts):
    op = m.get("outcomePrices")
    try: final = float(json.loads(op)[0]) if isinstance(op, str) else None
    except Exception: final = None
    if final is None or final < 0.5:
        return None  # only the WINNING outcome
    ids = m.get("clobTokenIds")
    if isinstance(ids, str): ids = json.loads(ids)
    if not ids: return None
    h = hist(ids[0])
    if len(h) < 5: return None
    p48 = price_at(h, end_ts - 48*3600)
    p24 = price_at(h, end_ts - 24*3600)
    cross = next((t for t, p in h if p >= 0.80), None)
    hrs_before = ((end_ts - cross)/3600) if cross else None
    return dict(name=(m.get("groupItemTitle") or m.get("question") or "")[:24],
                p48=p48, p24=p24, cross_hrs=hrs_before)


def main():
    cats = {
        "BoxOffice": ["opening weekend box office", "box office"],
        "RottenTomatoes": ["rotten tomatoes score"],
        "Netflix": ["top global netflix", "top us netflix"],
    }
    for cat, queries in cats.items():
        evs = {}
        for q in queries:
            for e in closed_events(q):
                evs[e.get("slug")] = e
        print(f"\n===== {cat}: {len(evs)} resolved events =====")
        rows = []
        for slug in list(evs)[:12]:
            e = full(slug) or evs[slug]
            try:
                end_ts = datetime.fromisoformat((e.get("endDate") or "").replace("Z","+00:00")).timestamp()
            except Exception:
                continue
            for m in e.get("markets") or []:
                r = analyze_market(m, end_ts)
                if r:
                    rows.append((e.get("title","")[:34].encode("ascii","replace").decode(), r))
                    break  # one winner per event
        if not rows:
            print("  no resolved markets with usable history.")
            continue
        print("  {:36} {:>7} {:>7} {:>10}".format("event", "p@T-48h", "p@T-24h", "cross0.8"))
        s48 = []; s24 = []
        for title, r in rows:
            c = f"{r['cross_hrs']:.0f}h before" if r['cross_hrs'] is not None else "never"
            p48 = r['p48'] if r['p48'] is not None else float('nan')
            p24 = r['p24'] if r['p24'] is not None else float('nan')
            if r['p48'] is not None: s48.append(r['p48'])
            if r['p24'] is not None: s24.append(r['p24'])
            print("  {:36} {:>7.2f} {:>7.2f} {:>10}".format(title, p48, p24, c))
        if s48:
            print(f"  AVG winner price  T-48h={sum(s48)/len(s48):.2f}  T-24h={sum(s24)/len(s24):.2f}  (n={len(s48)})")
            print("  -> closer to 1.00 = efficient (no edge); lower = exploitable lag")


if __name__ == "__main__":
    main()
