"""Gamma API market fetcher — normalizes Polymarket markets into the schema the prompt expects.

Gamma's market objects historically follow one row per market with a `tokens` array containing
YES/NO outcome tokens. Negative-risk multi-outcome markets are grouped under an `events` object
whose `markets` field references the per-outcome single-condition rows.

This module fetches active markets and exposes two views:
- a flat market view for single-condition (non-negRisk) markets
- a reconstituted multi-condition view for negRisk events, grouped by event id
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import requests

GAMMA_BASE = "https://gamma-api.polymarket.com"


def _hash_rules(description: str) -> str:
    return hashlib.sha256(description.encode("utf-8")).hexdigest()[:16]


def _liq(row: dict) -> float:
    """CLOB liquidity for a market row. Tokens with ~0 liquidity have no order book and
    will 404 on /book, so this is the key 'is it tradeable' signal."""
    for k in ("liquidityClob", "liquidityNum", "liquidity"):
        v = row.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


def _gamma_get(path: str, params: dict[str, Any]) -> Any | None:
    r = requests.get(f"{GAMMA_BASE}{path}", params=params, timeout=30)
    # Gamma returns 422 when offset overshoots the dataset; treat as end-of-data.
    if r.status_code == 422:
        return None
    r.raise_for_status()
    return r.json()


def fetch_active_events(
    limit_per_page: int = 100,
    max_events: int | None = None,
    *,
    min_volume: float = 1000.0,
    max_offset: int = 5000,
) -> list[dict]:
    """Fetch active events (groups of related markets) from Gamma.

    Events are the natural unit for neg-risk: a single event holds multiple markets that
    Polymarket presents as outcomes of a multi-condition question.

    ``min_volume`` filters long-tail events with negligible trading client-side; these
    have no arbitrage substrate. ``max_offset`` is a guardrail — Gamma's `active=true`
    flag can return tens of thousands of events; we cap so a single scan doesn't crawl
    the entire archive.
    """
    events: list[dict] = []
    offset = 0
    while offset < max_offset:
        page = _gamma_get(
            "/events",
            {"closed": "false", "active": "true", "limit": limit_per_page, "offset": offset},
        )
        if not page:
            break
        for ev in page:
            vol = float(ev.get("volume") or ev.get("volumeNum") or 0)
            if vol < min_volume:
                continue
            events.append(ev)
            if max_events and len(events) >= max_events:
                return events
        if len(page) < limit_per_page:
            break
        offset += limit_per_page
        time.sleep(0.1)
    return events


def _parse_tokens(market_row: dict) -> list[dict]:
    """Extract YES/NO outcome tokens from a Gamma market row.

    Gamma serializes `clobTokenIds` and `outcomes` as JSON strings in many responses.
    """
    raw_ids = market_row.get("clobTokenIds") or market_row.get("clob_token_ids") or "[]"
    raw_outcomes = market_row.get("outcomes") or "[]"
    if isinstance(raw_ids, str):
        try:
            ids = json.loads(raw_ids)
        except json.JSONDecodeError:
            ids = []
    else:
        ids = raw_ids or []
    if isinstance(raw_outcomes, str):
        try:
            outcomes = json.loads(raw_outcomes)
        except json.JSONDecodeError:
            outcomes = []
    else:
        outcomes = raw_outcomes or []
    return [{"token_id": tid, "outcome": out} for tid, out in zip(ids, outcomes)]


def normalize_single_condition_market(row: dict) -> dict | None:
    """Normalize a single binary market (one YES, one NO token) into prompt schema.

    Returns None if the row is missing required fields.
    """
    tokens = _parse_tokens(row)
    if not tokens:
        return None
    yes_token = next((t for t in tokens if t["outcome"].lower() == "yes"), tokens[0])
    description = row.get("description") or ""
    return {
        "condition_id": row.get("conditionId") or row.get("condition_id") or yes_token["token_id"],
        "title": row.get("question") or row.get("title") or "",
        "description": description,
        "end_date": row.get("endDateIso") or row.get("endDate"),
        "resolution_source": row.get("resolutionSource") or "unspecified",
        "neg_risk": False,
        "rules_hash": _hash_rules(description),
        "liquidity_usd": _liq(row),
        "conditions": [
            {
                "id": yes_token["token_id"],
                "question": row.get("question") or row.get("title") or "",
                "rules": description,
                "volume_usd": float(row.get("volume") or row.get("volumeNum") or 0),
                "liquidity_usd": _liq(row),
            }
        ],
    }


def normalize_neg_risk_event(event: dict) -> dict | None:
    """Reconstitute a multi-condition market from a neg-risk event.

    All sub-markets in a neg-risk event share resolution rules and are mutually exclusive.
    """
    sub_markets = event.get("markets") or []
    if len(sub_markets) < 2:
        return None
    description = event.get("description") or sub_markets[0].get("description") or ""
    conditions: list[dict] = []
    for sm in sub_markets:
        tokens = _parse_tokens(sm)
        yes_token = next((t for t in tokens if t["outcome"].lower() == "yes"), None)
        if not yes_token:
            continue
        conditions.append(
            {
                "id": yes_token["token_id"],
                "question": sm.get("groupItemTitle") or sm.get("question") or "",
                "rules": sm.get("description") or description,
                "volume_usd": float(sm.get("volume") or sm.get("volumeNum") or 0),
                "liquidity_usd": _liq(sm),
            }
        )
    if not conditions:
        return None
    return {
        "condition_id": event.get("id") or event.get("slug"),
        "title": event.get("title") or "",
        "description": description,
        "end_date": event.get("endDate"),
        "resolution_source": event.get("resolutionSource") or "unspecified",
        "neg_risk": True,
        "rules_hash": _hash_rules(description),
        "liquidity_usd": _liq(event),
        "conditions": conditions,
    }


def fetch_all_normalized_markets(
    max_events: int | None = None,
    *,
    min_liquidity: float = 500.0,
) -> list[dict]:
    """Fetch and normalize the active market universe.

    Each event yields either a single-condition market (if it has one sub-market) or a
    multi-condition market (if it has more). Returns the union.

    ``min_liquidity`` drops markets with no meaningful CLOB liquidity — their tokens have
    no order book (the /book endpoint 404s), so there is no arbitrage to capture and no
    point spending LLM budget classifying them.
    """
    events = fetch_active_events(max_events=max_events)
    out: list[dict] = []
    for ev in events:
        subs = ev.get("markets") or []
        if ev.get("negRisk") and len(subs) >= 2:
            m = normalize_neg_risk_event(ev)
            if m and m.get("liquidity_usd", 0.0) >= min_liquidity:
                out.append(m)
        else:
            for sm in subs:
                m = normalize_single_condition_market(sm)
                if m and m.get("liquidity_usd", 0.0) >= min_liquidity:
                    out.append(m)
    return out
