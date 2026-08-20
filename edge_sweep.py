"""Sweep ALL active Polymarket markets for INFORMATION-based edge candidates.

Buckets (most actionable first):
  STALE     : deadline already passed but still trading at non-0/1 price -> the answer is
              likely already knowable; collect the gap (resolution-lag edge).
  IMMINENT  : resolves within 72h at an uncertain price -> a knowable info event is near.
  EXTREME   : priced >0.92 or <0.08 with liquidity -> settlement-yield candidates.
  SCRAPEABLE: resolution text names a monitorable source (ISW / on-chain / API / data release).
"""
import json, requests
from datetime import datetime, timezone

GAMMA = "https://gamma-api.polymarket.com"
NOW = datetime.now(timezone.utc)

SCRAPE_HINTS = ["isw", "understandingwar", "on-chain", "onchain", "etherscan", "coingecko",
                "coinmarketcap", "dexscreener", "blockchain", "github", "api ", "bls.gov",
                "federal reserve", "fred", "cme", "espn", "according to"]


def yes_price(m):
    op = m.get("outcomePrices")
    if isinstance(op, str):
        try:
            arr = json.loads(op)
            return float(arr[0])
        except Exception:
            pass
    for k in ("lastTradePrice", "bestAsk", "bestBid"):
        v = m.get(k)
        if v not in (None, ""):
            try: return float(v)
            except Exception: pass
    return None


def days_to(iso):
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (dt - NOW).total_seconds() / 86400.0
    except Exception:
        return None


def main():
    stale, imminent, extreme, scrape = [], [], [], []
    seen = 0
    for off in range(0, 12000, 500):
        try:
            page = requests.get(f"{GAMMA}/markets",
                                params={"closed": "false", "active": "true",
                                        "limit": 500, "offset": off}, timeout=30).json()
        except Exception:
            break
        if not page:
            break
        for m in page:
            seen += 1
            p = yes_price(m)
            if p is None:
                continue
            d = days_to(m.get("endDate") or m.get("endDateIso"))
            vol = float(m.get("volume") or 0)
            liq = float(m.get("liquidity") or 0)
            q = m.get("question") or ""
            desc = (m.get("description") or "").lower()
            # STALE: deadline passed, still trading mid-range
            if d is not None and d < 0 and 0.08 < p < 0.92:
                stale.append((vol, d, p, q))
            # IMMINENT: resolves <72h, uncertain, some volume
            elif d is not None and 0 <= d < 3 and 0.10 < p < 0.90 and vol > 5000:
                imminent.append((vol, d, p, q))
            # EXTREME price with liquidity (settlement yield)
            if (p > 0.92 or p < 0.08) and liq > 5000 and (d is None or d > 1):
                extreme.append((liq, p, d, q))
            # SCRAPEABLE source
            if any(h in desc for h in SCRAPE_HINTS) and vol > 10000 and 0.05 < p < 0.95:
                scrape.append((vol, p, d, q))
        if len(page) < 500:
            break

    print(f"scanned {seen} active markets\n")

    def show(title, rows, key, fmt):
        rows.sort(key=key, reverse=True)
        print(f"=== {title} ({len(rows)}) ===")
        for r in rows[:15]:
            print(fmt(r))
        print()

    show("STALE (deadline passed, still mid-priced -> answer likely known)", stale,
         lambda r: r[0],
         lambda r: f"  vol ${r[0]:>9.0f}  {r[1]:>6.1f}d  p={r[2]:.2f}  {r[3][:60]}")
    show("IMMINENT (<72h, uncertain, liquid -> info event near)", imminent,
         lambda r: r[0],
         lambda r: f"  vol ${r[0]:>9.0f}  in {r[1]:.1f}d  p={r[2]:.2f}  {r[3][:60]}")
    show("EXTREME (>0.92/<0.08, liquid -> settlement-yield)", extreme,
         lambda r: r[0],
         lambda r: f"  liq ${r[0]:>9.0f}  p={r[1]:.3f}  {r[3][:62]}")
    show("SCRAPEABLE SOURCE (monitorable resolution feed)", scrape,
         lambda r: r[0],
         lambda r: f"  vol ${r[0]:>9.0f}  p={r[1]:.2f}  {r[3][:60]}")


if __name__ == "__main__":
    main()
