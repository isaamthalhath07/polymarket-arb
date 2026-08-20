"""Backtest the 'launch-watcher' edge.

Claim: 'Will X launch a token / perform an airdrop by [date]' markets sit cheap, then
SNAP toward $1 when the launch happens. The edge = detecting the launch in the gap
before the snap. This measures, on REAL price history:
  - did a sharp jump occur?  (low -> high)
  - entry price just before the jump   (what you'd buy at)
  - peak after                          (what it's worth)
  - how long the 0.3->0.7 transition took  = your reaction window
If transitions are SLOW (hours), a fast detector wins. If INSTANT (one step), no edge.

Data limit: Polymarket history is hourly for long windows, so sub-hour snaps show as one
candle (we flag those as 'INSTANT/unknown' — pessimistic).
"""
import requests, time

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
KW = ["launch a token", "launch its token", "airdrop", "perform an airdrop", "FDV above"]


def find_markets():
    out = {}
    for closed in ("true", "false"):
        for off in range(0, 6000, 500):
            try:
                page = requests.get(f"{GAMMA}/markets",
                                    params={"closed": closed, "limit": 500, "offset": off},
                                    timeout=30).json()
            except Exception:
                break
            if not page:
                break
            for m in page:
                q = (m.get("question") or "")
                if any(k.lower() in q.lower() for k in KW):
                    import json
                    ids = m.get("clobTokenIds")
                    if isinstance(ids, str):
                        try: ids = json.loads(ids)
                        except Exception: ids = []
                    if ids:
                        out[q] = (ids[0], closed == "true", float(m.get("volume") or 0))
            if len(page) < 500:
                break
    return out


def history(token):
    for params in ({"market": token, "interval": "max", "fidelity": 60},
                   {"market": token, "interval": "1m", "fidelity": 60}):
        try:
            r = requests.get(f"{CLOB}/prices-history", params=params, timeout=30)
            if r.status_code == 200:
                h = r.json().get("history", [])
                if len(h) >= 5:
                    return [(p["t"], float(p["p"])) for p in h]
        except Exception:
            pass
    return []


def analyze(h):
    """Find the biggest upward jump and the 0.3->0.7 transition window."""
    if len(h) < 5:
        return None
    # biggest single-step jump
    best = (0.0, None)
    for i in range(len(h) - 1):
        d = h[i+1][1] - h[i][1]
        if d > best[0]:
            best = (d, i)
    jump, i = best
    if i is None or jump < 0.15:
        return None  # no clear launch-style snap
    entry = h[i][1]
    peak = max(p for _, p in h[i:])
    # transition window: time from last point <=0.3 to first point >=0.7 around the jump
    t_lo = t_hi = None
    for t, p in h:
        if p <= 0.3:
            t_lo = t
        if p >= 0.7 and t_hi is None and (t_lo is not None):
            t_hi = t
            break
    window_min = ((t_hi - t_lo) / 60.0) if (t_lo and t_hi) else None
    return dict(entry=entry, peak=peak, jump=jump, gain=peak - entry, window_min=window_min,
                step_min=(h[i+1][0]-h[i][0])/60.0)


def main():
    print("[bt] finding launch/airdrop markets (active + closed)...")
    mk = find_markets()
    print(f"[bt] {len(mk)} markets; pulling price history...\n")
    rows = []
    for q, (tok, closed, vol) in mk.items():
        h = history(tok)
        if not h:
            continue
        a = analyze(h)
        if a:
            rows.append((a["gain"], a, q, closed, vol))
    rows.sort(key=lambda x: -x[0])
    print("{:>6} {:>6} {:>6} {:>9} {:>9} {:>6}  {}".format(
        "entry", "peak", "gain", "win(min)", "step(min)", "closed", "market"))
    print("-" * 100)
    snaps = 0; slow = 0
    for gain, a, q, closed, vol in rows[:30]:
        w = f"{a['window_min']:.0f}" if a['window_min'] is not None else "instant?"
        if a['window_min'] and a['window_min'] > 90:
            slow += 1
        else:
            snaps += 1
        print("{:>6.2f} {:>6.2f} {:>6.2f} {:>9} {:>9.0f} {:>6}  {}".format(
            a['entry'], a['peak'], a['gain'], w, a['step_min'], "Y" if closed else "N", q[:46]))
    print(f"\n{len(rows)} markets had a launch-style jump (>0.15).")
    print(f"  SLOW transitions (>90min window, exploitable): {slow}")
    print(f"  fast/instant (<=90min or one candle, hard):    {snaps}")
    print("\nNOTE: hourly data => sub-hour snaps look 'instant'. A true test needs the on-chain")
    print("launch timestamp vs market price; this measures the PRIZE (gain) and coarse window.")


if __name__ == "__main__":
    main()
