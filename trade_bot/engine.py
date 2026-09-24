"""Trade generation.

Every trade is scored from *your* point of view. The ratio is
receive_value / give_value, so a ratio above 1.0 means you gain value.

A trade only counts as realistic when its ratio falls inside the window
for its kind. The windows follow normal Rolimons trading norms:

* upgrade   (several of your items -> one bigger item): you usually overpay
  a little, so the ratio sits a bit under 1.0.
* downgrade (one of your items -> several smaller items): the other side
  usually overpays, so the ratio sits a bit over 1.0.
* sidegrade (one item for one item): close to even.

Inside a window, the trade that is best for you ranks first. The ranking
also weighs demand and trend, so a slightly smaller gain on a high-demand
item can beat a bigger gain on an item nobody wants.
"""

from __future__ import annotations

import bisect
import heapq
import itertools
import math
from dataclasses import dataclass, field

from .models import ItemInfo, OwnedItem

UPGRADE = "upgrade"
DOWNGRADE = "downgrade"
SIDEGRADE = "sidegrade"
KINDS = (UPGRADE, DOWNGRADE, SIDEGRADE)

TREND_SCORE = {-1: 0.0, 0: -1.0, 1: -0.3, 2: 0.0, 3: 0.5, 4: -0.3}


@dataclass
class TradeRules:
    upgrade_ratio: tuple[float, float] = (0.80, 0.95)
    downgrade_ratio: tuple[float, float] = (1.05, 1.20)
    sidegrade_ratio: tuple[float, float] = (0.97, 1.08)
    max_give: int = 4
    max_receive: int = 4
    min_receive_demand: int = -1
    min_item_value: int = 0
    allow_projected: bool = False
    allow_hyped: bool = False
    keep: set[int] = field(default_factory=set)  # asset IDs you refuse to trade away
    per_kind: int = 10
    search_budget: int = 20_000  # DFS nodes per anchor item


@dataclass
class Trade:
    kind: str
    give: list[ItemInfo]
    receive: list[ItemInfo]
    score: float = 0.0

    @property
    def give_value(self) -> int:
        return sum(i.trade_value for i in self.give)

    @property
    def receive_value(self) -> int:
        return sum(i.trade_value for i in self.receive)

    @property
    def ratio(self) -> float:
        return self.receive_value / self.give_value

    @property
    def gain(self) -> int:
        return self.receive_value - self.give_value

    def to_dict(self) -> dict:
        def items(xs):
            return [{"asset_id": i.asset_id, "name": i.name, "value": i.trade_value} for i in xs]

        return {
            "kind": self.kind,
            "give": items(self.give),
            "receive": items(self.receive),
            "give_value": self.give_value,
            "receive_value": self.receive_value,
            "gain": self.gain,
            "ratio": round(self.ratio, 4),
            "score": round(self.score, 4),
        }


def item_quality(item: ItemInfo) -> float:
    demand = item.demand if item.demand >= 0 else 0.5
    return demand + TREND_SCORE.get(item.trend, 0.0) + (0.5 if item.rare else 0.0)


def side_quality(items: list[ItemInfo]) -> float:
    total = sum(i.trade_value for i in items)
    return sum(item_quality(i) * i.trade_value for i in items) / total


def score_trade(trade: Trade) -> float:
    # Demand/trend nudges the ranking by up to ~20%; value is the main term.
    quality_delta = side_quality(trade.receive) - side_quality(trade.give)
    # Fewer items to manage is slightly better, all else equal.
    item_count_penalty = 0.005 * (len(trade.give) + len(trade.receive) - 2)
    return trade.ratio * (1 + 0.04 * quality_delta) - item_count_penalty


def _tradeable(item: ItemInfo, rules: TradeRules) -> bool:
    if item.trade_value <= 0 or item.trade_value < rules.min_item_value:
        return False
    if item.projected and not rules.allow_projected:
        return False
    return True


def giveable_pool(inventory: list[OwnedItem], rules: TradeRules) -> list[ItemInfo]:
    return [
        o.info
        for o in inventory
        if not o.on_hold and o.info.asset_id not in rules.keep and _tradeable(o.info, rules)
    ]


def receivable_pool(items: list[ItemInfo], rules: TradeRules) -> list[ItemInfo]:
    return [
        i
        for i in items
        if _tradeable(i, rules)
        and i.demand >= rules.min_receive_demand
        and (rules.allow_hyped or not i.hyped)
    ]


class ComboFinder:
    """Finds sets of 1..n items whose values sum inside [lo, hi].

    Duplicate copies of the same item are allowed, but the same multiset is
    never produced twice.
    """

    def __init__(self, items: list[ItemInfo]):
        self.items = sorted(items, key=lambda i: (-i.trade_value, i.asset_id))
        self.neg_values = [-i.trade_value for i in self.items]
        self.prefix = list(itertools.accumulate((i.trade_value for i in self.items), initial=0))

    def find(
        self,
        lo: float,
        hi: float,
        min_n: int,
        max_n: int,
        prefer_high: bool,
        limit: int,
        budget: int,
        max_item_value: float | None = None,
    ) -> list[list[ItemInfo]]:
        items, neg, prefix = self.items, self.neg_values, self.prefix
        size = len(items)
        best: list[tuple] = []  # heap of (key, tiebreak, combo); worst on top
        counter = itertools.count()
        nodes = 0
        # A total sitting right on our edge of the window can't be beaten.
        edge = math.floor(hi) if prefer_high else math.ceil(lo)

        def record(total: int, chosen: list[int]):
            # Prefer the total closest to our side of the window, then fewer items.
            key = (total if prefer_high else -total, -len(chosen))
            entry = (key, next(counter), [items[i] for i in chosen])
            if len(best) < limit:
                heapq.heappush(best, entry)
            elif key > best[0][0]:
                heapq.heapreplace(best, entry)

        def dfs(start: int, total: int, chosen: list[int]):
            nonlocal nodes
            nodes += 1
            n = len(chosen)
            if n >= min_n and lo <= total <= hi:
                record(total, chosen)
            if n == max_n or nodes > budget:
                return
            if len(best) == limit and best[0][0][0] == (edge if prefer_high else -edge):
                return
            slots = max_n - n
            i, prev = start, None
            while True:
                cap = hi - total
                if max_item_value is not None:
                    cap = min(cap, max_item_value)
                if not prefer_high and len(best) == limit:
                    # Adding items only raises the total, so anything bigger
                    # than our current cheapest find can't win.
                    cap = min(cap, -best[0][0][0] - total)
                # Skip straight to the first item small enough to fit.
                i = max(i, bisect.bisect_left(neg, -cap, start))
                while i < size and items[i].asset_id == prev:
                    i += 1  # same multiset as the branch we just explored
                if i >= size:
                    return
                # Largest total reachable from here: the next `slots` items (sorted desc).
                reachable = total + prefix[min(i + slots, size)] - prefix[i]
                if reachable < lo:
                    return  # everything after this is smaller still
                if prefer_high and len(best) == limit and reachable < best[0][0][0]:
                    return  # can't beat what we already have
                prev = items[i].asset_id
                chosen.append(i)
                dfs(i + 1, total + items[i].trade_value, chosen)
                chosen.pop()
                if nodes > budget or (len(best) == limit and best[0][0][0] == (edge if prefer_high else -edge)):
                    return
                i += 1

        dfs(0, 0, [])
        return [combo for _, _, combo in sorted(best, reverse=True)]


def _unique_assets(items: list[ItemInfo]) -> list[ItemInfo]:
    seen: dict[int, ItemInfo] = {}
    for i in items:
        seen.setdefault(i.asset_id, i)
    return list(seen.values())


def generate_trades(
    my_items: list[OwnedItem],
    their_items: list[ItemInfo],
    rules: TradeRules | None = None,
) -> dict[str, list[Trade]]:
    """Return the best trades of each kind, best first.

    my_items: your inventory (one entry per copy).
    their_items: what you could receive. One entry per copy for a real trade
        partner, or one entry per item for the whole Rolimons catalog.
    """
    rules = rules or TradeRules()
    give_pool = giveable_pool(my_items, rules)
    recv_pool = receivable_pool(their_items, rules)
    results: dict[str, list[Trade]] = {k: [] for k in KINDS}
    if not give_pool or not recv_pool:
        return results

    give_finder = ComboFinder(give_pool)
    recv_finder = ComboFinder(recv_pool)

    # Upgrades: several of mine -> one of theirs worth more than any single piece I give.
    lo_r, hi_r = rules.upgrade_ratio
    for target in _unique_assets(recv_pool):
        v = target.trade_value
        combos = give_finder.find(
            lo=v / hi_r, hi=v / lo_r, min_n=2, max_n=rules.max_give,
            prefer_high=False, limit=1, budget=rules.search_budget,
            max_item_value=v - 1,
        )
        results[UPGRADE] += [Trade(UPGRADE, combo, [target]) for combo in combos]

    # Downgrades: one of mine -> several of theirs, each worth less than what I give.
    lo_r, hi_r = rules.downgrade_ratio
    for mine in _unique_assets(give_pool):
        v = mine.trade_value
        combos = recv_finder.find(
            lo=v * lo_r, hi=v * hi_r, min_n=2, max_n=rules.max_receive,
            prefer_high=True, limit=1, budget=rules.search_budget,
            max_item_value=v - 1,
        )
        results[DOWNGRADE] += [Trade(DOWNGRADE, [mine], combo) for combo in combos]

    # Sidegrades: one for one.
    lo_r, hi_r = rules.sidegrade_ratio
    for mine in _unique_assets(give_pool):
        v = mine.trade_value
        for combo in recv_finder.find(
            lo=v * lo_r, hi=v * hi_r, min_n=1, max_n=1,
            prefer_high=True, limit=3, budget=rules.search_budget,
        ):
            if combo[0].asset_id != mine.asset_id:
                results[SIDEGRADE].append(Trade(SIDEGRADE, [mine], combo))

    for kind, trades in results.items():
        for t in trades:
            t.score = score_trade(t)
        trades.sort(key=lambda t: t.score, reverse=True)
        results[kind] = trades[: rules.per_kind]
    return results
