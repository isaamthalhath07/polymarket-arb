"""Comprehensive map of info-edge markets across ALL categories.
Groups open markets by detected resolution SOURCE (the thing you'd poll/scrape)."""
import requests, json, re
GAMMA = "https://gamma-api.polymarket.com"

QUERIES = [
    # culture/metrics
    "views","Billboard","box office","Netflix","Rotten Tomatoes","Spotify","YouTube","album sales",
    "opening weekend","IMDb","Metacritic","streams","first week",
    # weather/science
    "temperature","hurricane","snow","rain","tropical storm","weather","earthquake","high temperature",
    # crypto metrics
    "TVL","market cap","all-time high","dominance","stablecoin","gas fee","hashrate","active addresses",
    # tech/product
    "GitHub","release","version","ships","model","app store","downloads","subscribers","followers",
    # econ
    "CPI","unemployment","GDP","rate cut","jobs report","inflation","interest rate",
    # sports data
    "passing yards","points scored","goals","home runs",
]

SOURCES = {
    "RottenTomatoes": ["rotten tomatoes","tomatometer"],
    "TheNumbers/BoxOffice": ["the-numbers","box office mojo","box office"],
    "Netflix/FlixPatrol": ["top10.netflix","netflix top","flixpatrol"],
    "YouTube": ["youtube","views the latest","mrbeast"],
    "Billboard/Luminate": ["billboard","luminate","first week album"],
    "Spotify": ["spotify"],
    "NOAA/NWS-weather": ["national weather","noaa","nws","accuweather","wunderground","weather.gov","degrees"],
    "USGS-quake": ["usgs","earthquake","magnitude"],
    "DefiLlama/onchain": ["defillama","tvl","etherscan","coingecko","coinmarketcap","market cap","on-chain","onchain"],
    "Fed/BLS-econ": ["federal reserve","bls.gov","bureau of labor","cpi","fomc","unemployment"],
    "GitHub/official": ["github","official","press release","blog"],
}

def src_of(desc):
    d = (desc or "").lower()
    hits=[k for k,kw in SOURCES.items() if any(w in d for w in kw)]
    return hits[0] if hits else "OTHER/unclear"

def main():
    seen={}
    for q in QUERIES:
        try: data=requests.get(GAMMA+"/public-search",params={"q":q,"limit_per_type":20},timeout=20).json()
        except: continue
        for ev in data.get("events",[]):
            s=ev.get("slug")
            if s and not ev.get("closed") and s not in seen:
                seen[s]=ev
    print(f"unique open candidate markets: {len(seen)}")
    bysrc={}
    out=[]
    for s,ev in seen.items():
        try: e=requests.get(GAMMA+"/events",params={"slug":s},timeout=20).json()[0]
        except: e=ev
        desc=e.get("description","")
        src=src_of(desc)
        vol=float(e.get("volume") or 0)
        rec={"title":(e.get("title") or "").encode("ascii","replace").decode()[:60],
             "end":(e.get("endDate") or "")[:10],"vol":round(vol),"src":src,
             "n_out":len(e.get("markets") or [])}
        out.append(rec); bysrc.setdefault(src,[]).append(rec)
    print("\n=== markets grouped by RESOLUTION SOURCE (pollable feed) ===")
    for src in sorted(bysrc,key=lambda k:-sum(r["vol"] for r in bysrc[k])):
        rows=sorted(bysrc[src],key=lambda r:-r["vol"])
        tv=sum(r["vol"] for r in rows)
        print(f"\n## {src}  ({len(rows)} markets, ${tv:,.0f} vol)")
        for r in rows[:8]:
            print(f"   {r['end']}  ${r['vol']:>9,}  ({r['n_out']:>2}o)  {r['title']}")
    json.dump(out,open("edge_map.json","w"),indent=1)
    print(f"\nsaved {len(out)} markets to edge_map.json")

if __name__=="__main__":
    main()
