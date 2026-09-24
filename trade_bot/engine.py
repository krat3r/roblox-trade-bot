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
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .models import ItemInfo, OwnedItem

if TYPE_CHECKING:
    from .tradeads import TradeAd

UPGRADE = "upgrade"
DOWNGRADE = "downgrade"
SIDEGRADE = "sidegrade"
KINDS = (UPGRADE, DOWNGRADE, SIDEGRADE)

TREND_SCORE = {-1: 0.0, 0: -1.0, 1: -0.3, 2: 0.0, 3: 0.5, 4: -0.3}

# Roblox takes 30% of any Robux sent in a trade.
ROBUX_AFTER_TAX = 0.7
ROBUX_QUALITY = 2.0  # Robux never loses demand


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
    receive_robux: int = 0
    ad: TradeAd | None = None  # the Rolimons trade ad this trade answers, if any

    @property
    def give_value(self) -> int:
        return sum(i.trade_value for i in self.give)

    @property
    def robux_value(self) -> int:
        return int(self.receive_robux * ROBUX_AFTER_TAX)

    @property
    def receive_value(self) -> int:
        return sum(i.trade_value for i in self.receive) + self.robux_value

    @property
    def ratio(self) -> float:
        return self.receive_value / self.give_value

    @property
    def gain(self) -> int:
        return self.receive_value - self.give_value

    def to_dict(self) -> dict:
        def items(xs):
            return [{"asset_id": i.asset_id, "name": i.name, "value": i.trade_value} for i in xs]

        out = {
            "kind": self.kind,
            "give": items(self.give),
            "receive": items(self.receive),
            "receive_robux": self.receive_robux,
            "give_value": self.give_value,
            "receive_value": self.receive_value,
            "gain": self.gain,
            "ratio": round(self.ratio, 4),
            "score": round(self.score, 4),
        }
        if self.ad:
            out["ad"] = {
                "ad_id": self.ad.ad_id,
                "user_id": self.ad.user_id,
                "username": self.ad.username,
                "created": self.ad.created,
                "trade_url": self.ad.trade_url,
            }
        return out


def item_quality(item: ItemInfo) -> float:
    demand = item.demand if item.demand >= 0 else 0.5
    return demand + TREND_SCORE.get(item.trend, 0.0) + (0.5 if item.rare else 0.0)


def side_quality(items: list[ItemInfo], robux_value: int = 0) -> float:
    total = sum(i.trade_value for i in items) + robux_value
    weighted = sum(item_quality(i) * i.trade_value for i in items) + ROBUX_QUALITY * robux_value
    return weighted / total


def score_trade(trade: Trade) -> float:
    # Demand/trend nudges the ranking by up to ~20%; value is the main term.
    quality_delta = side_quality(trade.receive, trade.robux_value) - side_quality(trade.give)
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


def kind_for(n_give: int, n_receive: int) -> str:
    if n_give > n_receive:
        return UPGRADE
    if n_give < n_receive:
        return DOWNGRADE
    return SIDEGRADE


def _window(rules: TradeRules, kind: str) -> tuple[float, float]:
    return {UPGRADE: rules.upgrade_ratio, DOWNGRADE: rules.downgrade_ratio, SIDEGRADE: rules.sidegrade_ratio}[kind]


def _satisfies_tags(tags: set[str], give: list[ItemInfo]) -> bool:
    """Whether what you'd give matches what the poster asked for.

    "any" accepts anything. Otherwise any one of the item-quality tags they
    picked has to be met. Shape tags (upgrade/downgrade) and "adds" are
    handled by the search itself.
    """
    if "any" in tags:
        return True
    checks = {
        "demand": all(i.demand >= 2 for i in give),
        "rares": any(i.rare for i in give),
        "rap": all(not i.has_value for i in give),
    }
    wanted = [t for t in tags if t in checks]
    if not wanted:
        # Only shape tags, or tags we can't check from here (wishlist): value decides.
        return "wishlist" not in tags or bool(tags & {"upgrade", "downgrade", "adds"})
    return any(checks[t] for t in wanted)


def _answer_ad(ad: TradeAd, give_pool: list[ItemInfo], finder: ComboFinder, rules: TradeRules) -> Trade | None:
    """Best trade you can offer this ad's poster from your inventory, if any."""
    receive = ad.offer_items
    if any(i.projected and not rules.allow_projected for i in receive):
        return None
    if any(i.hyped and not rules.allow_hyped for i in receive):
        return None
    recv_value = sum(i.trade_value for i in receive) + int(ad.offer_robux * ROBUX_AFTER_TAX)
    if recv_value <= 0:
        return None
    n_recv = max(len(receive), 1)
    tags = set(ad.request_tags)

    if ad.request_items:
        # They named exactly what they want. You must own every piece.
        owned = Counter(i.asset_id for i in give_pool)
        wanted = Counter(i.asset_id for i in ad.request_items)
        if any(owned[a] < n for a, n in wanted.items()):
            return None
        trade = Trade(kind_for(len(ad.request_items), n_recv), list(ad.request_items), list(receive),
                      receive_robux=ad.offer_robux, ad=ad)
        # They asked for it, so any deal where you don't lose more than a normal overpay is fine.
        if trade.ratio < _window(rules, trade.kind)[0]:
            return None
        return trade

    # Tag-only ad ("any", "upgrade", "downgrade", ...): build the offer ourselves.
    if not tags or tags <= {"robux", "projecteds", "wishlist"}:
        return None  # we never pay Robux or give projecteds, and can't see their wishlist
    if "upgrade" in tags:
        # They want fewer, bigger items: one of yours for several of theirs, or 1:1 for something bigger.
        give_counts = [n for n in range(1, rules.max_give + 1) if n < n_recv or n == n_recv == 1]
    elif "downgrade" in tags:
        give_counts = [n for n in range(2, rules.max_give + 1) if n > n_recv]
    else:
        give_counts = list(range(1, rules.max_give + 1))

    biggest_offer = max((i.trade_value for i in receive), default=0)
    best: Trade | None = None
    for n in give_counts:
        kind = kind_for(n, n_recv)
        lo_r, hi_r = _window(rules, kind)
        # Keep the shape honest: an upgrade gives you something bigger than any piece you hand over.
        max_piece = biggest_offer - 1 if kind == UPGRADE else None
        for combo in finder.find(
            lo=recv_value / hi_r, hi=recv_value / lo_r, min_n=n, max_n=n,
            prefer_high=False, limit=1, budget=rules.search_budget, max_item_value=max_piece,
        ):
            if "upgrade" in tags and combo[0].trade_value <= biggest_offer:
                continue  # not an upgrade for them
            if kind == DOWNGRADE and combo[0].trade_value <= biggest_offer:
                continue
            if not _satisfies_tags(tags, combo):
                continue
            trade = Trade(kind, combo, list(receive), receive_robux=ad.offer_robux, ad=ad)
            trade.score = score_trade(trade)
            if best is None or trade.score > best.score:
                best = trade
    return best


def match_trade_ads(my_items: list[OwnedItem], ads: list[TradeAd], rules: TradeRules | None = None,
                    limit: int = 15) -> list[Trade]:
    """Trades you could send right now to players with live Rolimons trade ads, best first.

    One trade per poster, so the list isn't flooded by someone who spams ads.
    """
    rules = rules or TradeRules()
    give_pool = giveable_pool(my_items, rules)
    if not give_pool:
        return []
    finder = ComboFinder(give_pool)
    best_per_user: dict[int, Trade] = {}
    for ad in ads:
        trade = _answer_ad(ad, give_pool, finder, rules)
        if trade is None:
            continue
        trade.score = score_trade(trade)
        # Ads where they named your exact items are much likelier to be accepted.
        if ad.request_items:
            trade.score += 0.05
        current = best_per_user.get(ad.user_id)
        if current is None or trade.score > current.score:
            best_per_user[ad.user_id] = trade
    return sorted(best_per_user.values(), key=lambda t: t.score, reverse=True)[:limit]
