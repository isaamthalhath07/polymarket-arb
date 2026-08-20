from scanner.arb import compute_arb
from scanner.pricing import Book


def _book(tid: str, bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> Book:
    return Book(token_id=tid, bids=sorted(bids, key=lambda x: -x[0]), asks=sorted(asks, key=lambda x: x[0]))


def test_sum_equal_no_arb_when_balanced():
    # sum(S) ≈ sum(S'), tight spread, no edge
    s = [_book("a", bids=[(0.49, 100)], asks=[(0.51, 100)])]
    sp = [_book("b1", bids=[(0.24, 100)], asks=[(0.26, 100)]),
          _book("b2", bids=[(0.24, 100)], asks=[(0.26, 100)])]
    res = compute_arb(s, sp, "sum_equal", depth=3)
    assert res is None


def test_sum_equal_finds_arb():
    # sum_s_ask = 0.40; sum_sp_bid = 0.30 + 0.30 = 0.60. Buy S (cheap), sell S' (rich). Edge per unit = 0.20.
    s = [_book("a", bids=[(0.35, 100)], asks=[(0.40, 50)])]
    sp = [_book("b1", bids=[(0.30, 50)], asks=[(0.32, 50)]),
          _book("b2", bids=[(0.30, 50)], asks=[(0.32, 50)])]
    res = compute_arb(s, sp, "sum_equal", depth=3)
    assert res is not None
    assert res.edge_per_unit > 0.15
    # max units constrained by smallest filled level = 50
    assert res.max_units <= 50.0
    assert res.expected_profit_usd > 0


def test_s_implies_s_prime_one_way():
    # S=outcome (win); S'=margin buckets. P(S) must be <= P(S'). Arb if P(S) > P(S').
    # asks of S' = 0.10 + 0.10 = 0.20 ; bid of S = 0.50 ⇒ sell S, buy S'.
    s = [_book("win", bids=[(0.50, 100)], asks=[(0.55, 100)])]
    sp = [_book("m1", bids=[(0.05, 50)], asks=[(0.10, 50)]),
          _book("m2", bids=[(0.05, 50)], asks=[(0.10, 50)])]
    res = compute_arb(s, sp, "s_implies_s_prime", depth=3)
    assert res is not None
    assert "win" in res.side_sell
    assert set(res.side_buy) == {"m1", "m2"}
    assert res.edge_per_unit > 0.25


def test_s_implies_s_prime_no_arb_when_correctly_priced():
    # P(S)=0.20, P(S')=0.30; S implies S' so this is fine (S <= S'), no arb.
    s = [_book("win", bids=[(0.18, 100)], asks=[(0.20, 100)])]
    sp = [_book("m1", bids=[(0.14, 50)], asks=[(0.16, 50)]),
          _book("m2", bids=[(0.14, 50)], asks=[(0.16, 50)])]
    res = compute_arb(s, sp, "s_implies_s_prime", depth=3)
    assert res is None


def test_empty_books_returns_none():
    assert compute_arb([], [], "sum_equal") is None
