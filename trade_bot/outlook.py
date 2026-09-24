"""Growth outlook: which items (and so which trades) are likelier to rise in value.

Rolimons' public API is a snapshot with no price history, so this is an
estimate built from the signals traders watch, not a guarantee:

* trend: Rolimons marks items as raising, stable, unstable, fluctuating or lowering
* demand: high-demand items are the ones that get value raises
* RAP vs value: when buyers pay more than the listed value, the value
  tends to be raised to match, and vice versa
* rare items hold and grow; hyped items tend to fall back; projected
  items' RAP is manipulated and usually crashes
* momentum: how much RAP moved since the oldest snapshot the bot has
  saved locally (see history.py). This one gets better the more you run it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import ItemInfo

if TYPE_CHECKING:
    from .engine import Trade

TREND_OUTLOOK = {3: 1.0, 2: 0.0, 4: -0.2, 1: -0.3, 0: -1.0, -1: 0.0}
DEMAND_OUTLOOK = {4: 0.8, 3: 0.5, 2: 0.1, 1: -0.3, 0: -0.6, -1: -0.2}


def _clamp(x: float, limit: float) -> float:
    return max(-limit, min(limit, x))


class Outlook:
    def __init__(self, momentum: dict[int, float] | None = None):
        # asset_id -> fractional RAP change over the saved history window
        self.momentum = momentum or {}

    def item(self, item: ItemInfo) -> float:
        score = TREND_OUTLOOK.get(item.trend, 0.0) + DEMAND_OUTLOOK.get(item.demand, 0.0)
        if item.has_value and item.rap > 0:
            score += _clamp((item.rap - item.value) / item.value, 0.5)
        if item.rare:
            score += 0.3
        if item.hyped:
            score -= 0.5
        if item.projected:
            score -= 1.0
        if item.asset_id in self.momentum:
            score += _clamp(self.momentum[item.asset_id] * 2, 0.6)
        return score

    def side(self, items: list[ItemInfo], robux_value: int = 0) -> float:
        """Value-weighted outlook of one side of a trade. Robux stays flat (0)."""
        total = sum(i.trade_value for i in items) + robux_value
        if total <= 0:
            return 0.0
        return sum(self.item(i) * i.trade_value for i in items) / total

    def trade(self, trade: Trade) -> float:
        """Positive when what you receive is likelier to grow than what you give."""
        return self.side(trade.receive, trade.robux_value) - self.side(trade.give)

    def rank(self, trade: Trade) -> float:
        """Ranking for long-term trades: growth first, but value still counts."""
        return self.trade(trade) + 2 * (trade.ratio - 1)


def outlook_label(score: float) -> str:
    if score >= 1.0:
        return "Strong"
    if score >= 0.4:
        return "Good"
    if score > -0.4:
        return "Neutral"
    if score > -1.0:
        return "Weak"
    return "Poor"
