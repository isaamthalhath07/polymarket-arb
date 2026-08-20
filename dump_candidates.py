"""Run the cheap stages (fetch + embed + prefilter) and dump top candidate pairs
with full market detail to candidates.json, so a human/stronger model can classify them
instead of the slow local 7B."""
import argparse
import json

from dotenv import load_dotenv
load_dotenv()

from scanner.cache import Cache
from scanner.markets import fetch_all_normalized_markets
from scanner.prefilter import candidate_pairs, embed_markets


def trim(m: dict) -> dict:
    return {
        "condition_id": m["condition_id"],
        "title": m["title"],
        "neg_risk": m.get("neg_risk", False),
        "description": (m.get("description") or "")[:500],
        "liquidity_usd": round(m.get("liquidity_usd", 0.0)),
        "conditions": [
            {
                "id": c["id"],
                "q": c["question"],
                "vol": round(c.get("volume_usd", 0.0)),
            }
            for c in m["conditions"]
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-events", type=int, default=40)
    ap.add_argument("--min-liquidity", type=float, default=500.0)
    ap.add_argument("--top-pairs", type=int, default=40)
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--min-cosine", type=float, default=0.45)
    ap.add_argument("--out", default="candidates.json")
    a = ap.parse_args()

    cache = Cache("scanner.db")
    print("[dump] fetching markets...")
    markets = fetch_all_normalized_markets(max_events=a.max_events, min_liquidity=a.min_liquidity)
    for m in markets:
        cache.upsert_market(m)
    print(f"[dump] {len(markets)} liquid markets")

    print("[dump] embedding...")
    emb = embed_markets(markets, cache)
    pairs = candidate_pairs(markets, emb, top_k=a.top_k, min_cosine=a.min_cosine)
    print(f"[dump] {len(pairs)} candidate pairs; writing top {a.top_pairs}")

    by_cid = {m["condition_id"]: m for m in markets}
    out = []
    for i, (a_cid, b_cid, sim) in enumerate(pairs[: a.top_pairs]):
        out.append({
            "pair_index": i,
            "similarity": round(sim, 3),
            "m1": trim(by_cid[a_cid]),
            "m2": trim(by_cid[b_cid]),
        })
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    cache.close()
    print(f"[dump] wrote {len(out)} pairs to {a.out}")


if __name__ == "__main__":
    main()
