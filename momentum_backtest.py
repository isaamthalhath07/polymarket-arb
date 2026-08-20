"""RESEARCH: does buying a price 'spike to 90%+' work?  (favorite-momentum backtest)

Hypothesis: when an outcome's price crosses a high threshold (0.80/0.90/0.95), the market
has 'decided' -> it should resolve YES. We buy at the crossing and collect $1.
Risk: false spikes that REVERT (cross high, then crash to 0) wipe out many small wins.

For each RESOLVED binary market we pull price history, find the FIRST upward crossing of
each threshold, and check the actual outcome (ended ~1 = YES, ~0 = NO). Then compute hit
rate and net P&L per trade (buy at crossing price, payoff = outcome, minus taker fee).

Buying at price p, fee ~= rate * p * (1-p) (Polymarket formula; tiny near extremes).
Breakeven hit-rate at buy price p is ~ p, so we report whether observed hit rate beats it.
Pure historical analysis. No trading.
"""
import requests, json
GAMMA="https://gamma-api.polymarket.com"; CLOB="https://clob.polymarket.com"
FEE_RATE=0.0125  # culture/weather taker ~1.25% (worst-ish; many cats lower)
THRESHOLDS=[0.80,0.90,0.95]
MAX_TOKENS=240

QUERIES=["spotify","billboard","box office","rotten tomatoes","netflix","album","movie",
 "election","governor","senate","will","champion","award","by june","by may","released",
 "winner","nominee","price","reach","hit"]

def active_tokens():
    """sampling-markets (active) reliably return price history."""
    d=requests.get(CLOB+"/sampling-markets",timeout=30).json()
    ms=d.get("data",d) if isinstance(d,dict) else d
    toks=[]
    for m in ms:
        for t in (m.get("tokens") or []):
            if (t.get("outcome") or "").lower()=="yes" and t.get("token_id"):
                toks.append(t["token_id"]); break
    return toks

def hist(tok):
    try:
        r=requests.get(CLOB+"/prices-history",params={"market":tok,"interval":"max","fidelity":60},timeout=25)
        return [(p["t"],float(p["p"])) for p in r.json().get("history",[])] if r.status_code==200 else []
    except: return []

def main():
    toks=active_tokens()
    print(f"active reward tokens: {len(toks)}; pulling history for up to {MAX_TOKENS}...")
    series=[]
    for tk in toks[:MAX_TOKENS]:
        h=hist(tk)
        if len(h)>=8: series.append(h)
    print(f"usable price series: {len(series)}")
    # 'effectively decided' = current/last price pinned near 0 or 1
    decided=[h for h in series if h[-1][1]>=0.92 or h[-1][1]<=0.08]
    print(f"effectively-decided series (last px <=0.08 or >=0.92): {len(decided)}\n")

    HORIZON=168*3600  # 7 days after the crossing
    print("UNBIASED test on ALL series: after an upward crossing, price 7 days later.\n")
    print("{:>6} {:>8} {:>8} {:>8} {:>8} {:>10} {:>11}".format(
        "thresh","signals","held>=.85","drift.5-.85","revert<.5","mean_fwd","net/trade"))
    print("-"*70)
    for th in THRESHOLDS:
        signals=held=drift=revert=0; fwd_sum=0.0; pnl=0.0
        for h in series:
            prev=h[0][1]; cross_i=None
            for i in range(1,len(h)):
                if prev<th<=h[i][1]: cross_i=i; break
                prev=h[i][1]
            if cross_i is None: continue
            ct=h[cross_i][0]; cp=h[cross_i][1]
            # price ~HORIZON later (last point at/after ct+HORIZON, else final)
            fwd=None
            for t,p in h[cross_i:]:
                fwd=p
                if t>=ct+HORIZON: break
            signals+=1; fwd_sum+=fwd
            if fwd>=0.85: held+=1
            elif fwd>=0.5: drift+=1
            else: revert+=1
            buy=min(cp,0.985); fee=FEE_RATE*buy*(1-buy)
            pnl+=(fwd-buy-fee)   # mark-to-market 7d later (not final resolution)
        if signals==0:
            print(f"{th:>6}   no signals"); continue
        print("{:>6.2f} {:>8} {:>8} {:>8} {:>8} {:>10.3f} {:>+11.4f}".format(
            th,signals,held,drift,revert,fwd_sum/signals,pnl/signals))
    print("\nrevert<.5 = false spikes that crashed (the steamroller).")
    print("net/trade here is mark-to-market 7d later, NOT final resolution.")

if __name__=="__main__":
    main()
