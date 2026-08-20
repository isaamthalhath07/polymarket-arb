# Polymarket Dependency-Driven Arbitrage Scanner

Scans active Polymarket markets, uses a formal-logic prompt over Claude to identify
*logically dependent* pairs (e.g. "X wins" vs "X wins by ≥5%"), pulls CLOB order
books for the dependent pairs, and ranks actionable arbitrage opportunities by
expected profit after slippage.

## Install

```powershell
cd C:\Users\isaam\polymarket_arb
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# edit .env, add GEMINI_API_KEY (get one at https://aistudio.google.com/apikey)
```

## Run

```powershell
# one-shot scan
python scan.py

# daemon mode, 5-min cycle
python scan.py --daemon --interval 300

# classify only (no order books, no $)
python scan.py --dry-run --max-llm-calls 20

# tune thresholds
python scan.py --min-cosine 0.5 --min-edge 0.02 --min-profit 10
```

## Architecture

| File | Purpose |
| --- | --- |
| `polymarket_dependency_prompt.md` | Cached Claude system prompt (formal-logic dependency rubric) |
| `scanner/markets.py` | Gamma API client, neg-risk event reconstitution |
| `scanner/prefilter.py` | Local MiniLM embeddings, cosine top-K candidate pairs |
| `scanner/classifier.py` | Google Gemini SDK with JSON-mode output |
| `scanner/reduce.py` | Rule 6 — top-4 conditions by volume + synthetic OTHER |
| `scanner/pricing.py` | py_clob_client wrapper for order books |
| `scanner/arb.py` | Edge sizing across `sum_equal` / `s_implies_s_prime` relations |
| `scanner/cache.py` | SQLite — markets, embeddings, classifications, arb snapshots |
| `scanner/budget.py` | Hard cap on LLM call count and spend |
| `scan.py` | CLI entrypoint |

## Tests

```powershell
pip install pytest
pytest
```

## Notes

- Polymarket retail taker fee is currently 0; the `fee_bps` parameter in `arb.py`
  is a safety knob if that changes.
- Order book depth is capped at top 3 levels per token by default. Polymarket books
  can be thin — sizing is conservative on purpose.
- Cache invalidation is keyed on `sha256(description)[:16]`. If Polymarket edits a
  market's rules, the classification will be re-run on the next cycle.
- Default model is `gemini-2.5-flash` (cheap, fast). Bump to `gemini-2.5-pro` via
  the `CLASSIFIER_MODEL` env var for higher reasoning quality on borderline pairs.
- Gemini's explicit context cache is not used in v1 — the ~10k-token rubric is
  re-sent every call. At Flash pricing this is well under a cent per pair.
