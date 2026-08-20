"""Price the dependent pairs I (the analyst) identified, and check for arbitrage.
Relation for every pair is s_implies_s_prime: S (earlier/subset) implies S' (later/superset),
so Price(S) <= Price(S'); arb iff bid(S) > ask(S')."""
import json
from dotenv import load_dotenv
load_dotenv()
from scanner.pricing import fetch_books
from scanner.arb import compute_arb

# pair_index -> which market is the implier S (earlier deadline / subset)
DEP = {
 0:'m2', 1:'m2', 2:'m1', 3:'m2', 4:'m1', 5:'m2', 6:'m2', 8:'m1', 9:'m2',
 10:'m1', 11:'m2', 12:'m1', 13:'m2', 15:'m2', 17:'m2', 18:'m2', 19:'m1',
 21:'m2', 22:'m1', 23:'m1', 24:'m1',
}

d = {p['pair_index']: p for p in json.load(open('candidates.json', encoding='utf-8'))}

# collect all tokens to fetch once
toks = set()
plan = []
for idx, early in DEP.items():
    if idx not in d:
        continue
    p = d[idx]
    s_m = p[early]
    sp_m = p['m2' if early == 'm1' else 'm1']
    s_tok = s_m['conditions'][0]['id']
    sp_tok = sp_m['conditions'][0]['id']
    toks.add(s_tok); toks.add(sp_tok)
    plan.append((idx, s_m['title'], sp_m['title'], s_tok, sp_tok))

print(f"fetching {len(toks)} order books...")
books = fetch_books(list(toks))

rows = []
for idx, s_title, sp_title, s_tok, sp_tok in plan:
    sb = books.get(s_tok); pb = books.get(sp_tok)
    if not sb or not pb:
        continue
    s_bid, sp_ask = sb.best_bid, pb.best_ask
    # quick price relationship snapshot
    viol = (s_bid is not None and sp_ask is not None and s_bid > sp_ask)
    res = compute_arb([sb], [pb], "s_implies_s_prime", depth=5)
    edge = f"${res.expected_profit_usd:.2f}" if res else "-"
    eu = f"{res.edge_per_unit:.3f}" if res else "-"
    rows.append((
        idx,
        f"{s_title[:34]} (early)",
        f"{sp_title[:34]} (late)",
        f"{s_bid}", f"{sp_ask}",
        "ARB!" if res else ("viol?" if viol else "ok"),
        eu, edge,
    ))

# sort: arbs first
rows.sort(key=lambda r: (r[5] != "ARB!", r[0]))
header = "{:>3} {:36} {:36} {:>6} {:>6} {:>5} {:>7} {:>8}".format(
    "idx", "S (earlier, implies)", "Sprime (later)", "bidS", "askSp", "flag", "edge/u", "profit")
print("\n" + header)
print("-"*120)
for r in rows:
    print("{:>3} {:36} {:36} {:>6} {:>6} {:>5} {:>7} {:>8}".format(*r))
