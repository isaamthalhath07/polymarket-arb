"""Rule 6: keep top-4 conditions by volume; collapse rest into synthetic OTHER."""
from __future__ import annotations

from copy import deepcopy

OTHER_SYNTH_ID = "OTHER_SYNTH"


def reduce_to_top4(market: dict) -> dict:
    """Return a copy of the market with at most 4 explicit conditions + a synthetic catch-all.

    The synthetic 5th condition is opaque per Rule 6; the prompt instructs the classifier not
    to reason about its internal content.
    """
    m = deepcopy(market)
    conds = sorted(m["conditions"], key=lambda c: -float(c.get("volume_usd", 0)))
    if len(conds) <= 4:
        m["conditions"] = conds
        return m
    top4 = conds[:4]
    rest = conds[4:]
    top4.append(
        {
            "id": OTHER_SYNTH_ID,
            "question": f"Any of {len(rest)} other conditions resolves TRUE",
            "rules": "Synthetic catch-all; do not reason about internal content.",
            "volume_usd": sum(float(c.get("volume_usd", 0)) for c in rest),
        }
    )
    m["conditions"] = top4
    return m


def prompt_payload(market: dict) -> dict:
    """Strip cache/internal fields, return the payload the LLM sees."""
    return {
        "title": market["title"],
        "description": market["description"],
        "end_date": market.get("end_date"),
        "resolution_source": market.get("resolution_source", "unspecified"),
        "neg_risk": market.get("neg_risk", False),
        "conditions": market["conditions"],
    }
