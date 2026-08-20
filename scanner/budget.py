"""LLM call budget tracker with hard cap."""
from __future__ import annotations

from dataclasses import dataclass


class BudgetExceeded(Exception):
    pass


@dataclass
class Budget:
    max_calls: int
    calls: int = 0
    spent_usd: float = 0.0

    def check(self) -> None:
        if self.calls >= self.max_calls:
            raise BudgetExceeded(
                f"hit hard cap of {self.max_calls} LLM calls; spent ${self.spent_usd:.4f}"
            )

    def record(self, cost_usd: float) -> None:
        self.calls += 1
        self.spent_usd += cost_usd

    def summary(self) -> str:
        return f"{self.calls} LLM calls / cap {self.max_calls}, spent ${self.spent_usd:.4f}"
