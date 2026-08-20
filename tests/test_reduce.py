from scanner.reduce import OTHER_SYNTH_ID, reduce_to_top4


def _mk(n: int) -> dict:
    return {
        "condition_id": "M",
        "title": "T",
        "description": "D",
        "conditions": [
            {"id": f"C{i}", "question": f"Q{i}", "rules": "r", "volume_usd": float(n - i)}
            for i in range(n)
        ],
    }


def test_passthrough_when_le_4():
    m = _mk(3)
    out = reduce_to_top4(m)
    assert len(out["conditions"]) == 3
    assert all(c["id"] != OTHER_SYNTH_ID for c in out["conditions"])


def test_exactly_4_no_synth():
    m = _mk(4)
    out = reduce_to_top4(m)
    assert len(out["conditions"]) == 4
    assert all(c["id"] != OTHER_SYNTH_ID for c in out["conditions"])


def test_collapses_rest_into_synth():
    m = _mk(7)
    out = reduce_to_top4(m)
    assert len(out["conditions"]) == 5
    assert out["conditions"][-1]["id"] == OTHER_SYNTH_ID
    # top 4 should be the 4 highest-volume (C0..C3 since volumes are n..1)
    top_ids = {c["id"] for c in out["conditions"][:4]}
    assert top_ids == {"C0", "C1", "C2", "C3"}
    # synth volume = sum of remaining
    expected = float(3 + 2 + 1)
    assert out["conditions"][-1]["volume_usd"] == expected


def test_does_not_mutate_input():
    m = _mk(6)
    before = len(m["conditions"])
    reduce_to_top4(m)
    assert len(m["conditions"]) == before
