"""Arbitrage edge calculator over a dependent subset relation and a set of order books.

Three relations from the prompt schema:

- ``sum_equal`` (biconditional): sum(YES in S) == sum(YES in S'). Buy the cheap side at
  ask, sell the dear side at bid. Profit per unit = |sum_asks_low - sum_bids_high| - fees.
- ``s_implies_s_prime`` (one-way): S YES implies S' YES, so price(S) <= price(S'). Arb if
  sum(asks in S') < sum(bids in S) — sell S, buy S'.
- ``s_prime_implies_s`` (one-way, reversed): symmetric to the above.

Polymarket retail taker fee is currently 0; we still parameterize fee_bps for safety.
Sizing walks each book level conservatively, capped at top ``depth`` levels per token.
"""
from __future__ import annotations

from dataclasses import dataclass

from .pricing import Book


@dataclass
class ArbResult:
    edge_per_unit: float        # USD profit per 1 unit of the arb basket
    max_units: float            # max basket size given order book depth
    expected_profit_usd: float  # edge_per_unit * max_units (after fees)
    side_buy: list[str]         # token ids to buy at ask
    side_sell: list[str]        # token ids to sell at bid
    notes: str

    def to_dict(self) -> dict:
        return {
            "edge_per_unit": self.edge_per_unit,
            "max_units": self.max_units,
            "expected_profit_usd": self.expected_profit_usd,
            "side_buy": self.side_buy,
            "side_sell": self.side_sell,
            "notes": self.notes,
        }


def _walk_buy(book: Book, units: float, depth: int) -> tuple[float, float]:
    """Walk asks; return (avg_price_for_filled_units, units_fillable)."""
    filled = 0.0
    cost = 0.0
    for price, size in book.asks[:depth]:
        take = min(size, units - filled)
        if take <= 0:
            break
        cost += take * price
        filled += take
        if filled >= units:
            break
    if filled == 0:
        return float("inf"), 0.0
    return cost / filled, filled


def _walk_sell(book: Book, units: float, depth: int) -> tuple[float, float]:
    """Walk bids; return (avg_price_for_filled_units, units_fillable)."""
    filled = 0.0
    proceeds = 0.0
    for price, size in book.bids[:depth]:
        take = min(size, units - filled)
        if take <= 0:
            break
        proceeds += take * price
        filled += take
        if filled >= units:
            break
    if filled == 0:
        return 0.0, 0.0
    return proceeds / filled, filled


def _max_size_across_buys(books: list[Book], depth: int) -> float:
    """Max units we can buy across each book given depth."""
    sizes = []
    for b in books:
        s = sum(size for _, size in b.asks[:depth])
        sizes.append(s)
    return min(sizes) if sizes else 0.0


def _max_size_across_sells(books: list[Book], depth: int) -> float:
    sizes = []
    for b in books:
        s = sum(size for _, size in b.bids[:depth])
        sizes.append(s)
    return min(sizes) if sizes else 0.0


def compute_arb(
    s_books: list[Book],
    s_prime_books: list[Book],
    relation: str,
    *,
    fee_bps: float = 0.0,
    depth: int = 3,
) -> ArbResult | None:
    """Compute the edge per unit and max profitable basket size.

    Conventions:
    - ``s_books`` and ``s_prime_books`` are the YES-token books for each side's conditions.
    - "1 unit of basket" means buying 1 share of each token on the buy side and selling 1 share of each on the sell side.
    - Side selection: which side to buy and which to sell is determined by the relation and current prices.
    """
    if not s_books or not s_prime_books:
        return None

    sum_s_ask = sum(b.best_ask or 1.0 for b in s_books)
    sum_s_bid = sum(b.best_bid or 0.0 for b in s_books)
    sum_sp_ask = sum(b.best_ask or 1.0 for b in s_prime_books)
    sum_sp_bid = sum(b.best_bid or 0.0 for b in s_prime_books)

    if relation == "sum_equal":
        # sum(S) == sum(S'). Two directions:
        #   buy S, sell S' if sum_s_ask < sum_sp_bid
        #   buy S', sell S if sum_sp_ask < sum_s_bid
        opt_a = sum_sp_bid - sum_s_ask
        opt_b = sum_s_bid - sum_sp_ask
        if opt_a >= opt_b and opt_a > 0:
            buy_side, sell_side = s_books, s_prime_books
        elif opt_b > opt_a and opt_b > 0:
            buy_side, sell_side = s_prime_books, s_books
        else:
            return None
    elif relation == "s_implies_s_prime":
        # sum(S) <= sum(S'). Arb iff sum_s_bid > sum_sp_ask: sell S, buy S'.
        if sum_s_bid - sum_sp_ask <= 0:
            return None
        buy_side, sell_side = s_prime_books, s_books
    elif relation == "s_prime_implies_s":
        if sum_sp_bid - sum_s_ask <= 0:
            return None
        buy_side, sell_side = s_books, s_prime_books
    else:
        return None

    max_buy = _max_size_across_buys(buy_side, depth)
    max_sell = _max_size_across_sells(sell_side, depth)
    max_units = min(max_buy, max_sell)
    if max_units <= 0:
        return None

    # Binary search would be more precise; for v1 we evaluate at max_units directly and
    # also at smaller fractions to find the size where edge stays positive.
    best: ArbResult | None = None
    fractions = [1.0, 0.75, 0.5, 0.25, 0.1]
    for f in fractions:
        units = max_units * f
        if units <= 0:
            continue
        buy_cost = 0.0
        buy_ok = True
        for b in buy_side:
            avg, filled = _walk_buy(b, units, depth)
            if filled < units - 1e-9:
                buy_ok = False
                break
            buy_cost += avg * units
        if not buy_ok:
            continue
        sell_proceeds = 0.0
        sell_ok = True
        for b in sell_side:
            avg, filled = _walk_sell(b, units, depth)
            if filled < units - 1e-9:
                sell_ok = False
                break
            sell_proceeds += avg * units
        if not sell_ok:
            continue
        gross = sell_proceeds - buy_cost
        fee_cost = (sell_proceeds + buy_cost) * (fee_bps / 10000.0)
        net = gross - fee_cost
        if net <= 0:
            continue
        edge_per_unit = net / units
        res = ArbResult(
            edge_per_unit=edge_per_unit,
            max_units=units,
            expected_profit_usd=net,
            side_buy=[b.token_id for b in buy_side],
            side_sell=[b.token_id for b in sell_side],
            notes=f"relation={relation} depth={depth}",
        )
        if best is None or res.expected_profit_usd > best.expected_profit_usd:
            best = res
    return best
