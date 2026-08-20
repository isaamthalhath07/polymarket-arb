import json
from pathlib import Path

from scanner.classifier import PROMPT_PATH, _build_user_payload


def test_prompt_file_exists():
    assert PROMPT_PATH.exists()
    text = PROMPT_PATH.read_text(encoding="utf-8")
    assert "# ROLE" in text
    assert "# OUTPUT FORMAT" in text
    # Instructions file must NOT contain the input placeholders any more — those go in user msg
    assert "{M1_JSON}" not in text
    assert "{M2_JSON}" not in text


def test_user_payload_contains_both_markets():
    m1 = {
        "condition_id": "AAA",
        "title": "M1",
        "description": "d1",
        "end_date": "2026-12-31",
        "resolution_source": "AP",
        "neg_risk": False,
        "conditions": [{"id": "t1", "question": "M1?", "rules": "r", "volume_usd": 1.0}],
    }
    m2 = {
        "condition_id": "BBB",
        "title": "M2",
        "description": "d2",
        "end_date": "2026-12-31",
        "resolution_source": "AP",
        "neg_risk": False,
        "conditions": [{"id": "t2", "question": "M2?", "rules": "r", "volume_usd": 1.0}],
    }
    payload = _build_user_payload(m1, m2)
    assert payload.startswith("Market 1:")
    assert "Market 2:" in payload
    # Validate the embedded JSON parses
    parts = payload.split("Market 2:")
    j1 = json.loads(parts[0].replace("Market 1:", "", 1).strip())
    j2 = json.loads(parts[1].strip())
    assert j1["title"] == "M1"
    assert j2["title"] == "M2"
    assert j1["conditions"][0]["id"] == "t1"


def test_local_prompt_is_compact():
    # For local CPU inference the rubric must stay small so prompt ingestion is fast.
    # Target: well under ~3k tokens (~12k chars) while keeping the required sections.
    text = PROMPT_PATH.read_text(encoding="utf-8")
    assert len(text) < 12_000, "local prompt too large for fast CPU inference"
    assert "step4_dependence" in text
    assert "dependent_subsets" in text
