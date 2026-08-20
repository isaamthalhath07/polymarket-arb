"""Price every unique market in the candidate set; print YES bid/ask/mid sorted by title
so dependency families cluster and price-monotonicity violations are visible."""
import json
from dotenv import load_dotenv
load_dotenv()
from scanner.pricing import fetch_books

d = json.load(open('candidates_wide.json', encoding='utf-8'))
uniq = {}
for p in d:
    for k in ('m1', 'm2'):
        m = p[k]
        if m['neg_risk']:
            continue  # skip multi-condition for this single-token price view
        cid = m['condition_id']
        if cid not in uniq:
            uniq[cid] = (m['title'], m['conditions'][0]['id'], round(m.get('liquidity_usd', 0)))

print(f"pricing {len(uniq)} unique single-condition markets...")
toks = [v[1] for v in uniq.values()]
books = fetch_books(toks)

rows = []
for cid, (title, tok, liq) in uniq.items():
    b = books.get(tok)
    if not b:
        continue
    rows.append((title, b.best_bid, b.best_ask, b.mid, liq))

rows.sort(key=lambda r: r[0])
print("{:54} {:>6} {:>6} {:>6} {:>9}".format("market", "bid", "ask", "mid", "liq$"))
print("-" * 90)
for title, bid, ask, mid, liq in rows:
    bs = f"{bid:.3f}" if bid is not None else "-"
    as_ = f"{ask:.3f}" if ask is not None else "-"
    ms = f"{mid:.3f}" if mid is not None else "-"
    print("{:54} {:>6} {:>6} {:>6} {:>9}".format(title[:54], bs, as_, ms, liq))
