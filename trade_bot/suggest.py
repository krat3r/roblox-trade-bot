"""The main flow: live trade ads first, general market suggestions as a fallback."""

from __future__ import annotations

import dataclasses
import sys
from dataclasses import dataclass

from . import history, tradeads
from .engine import KINDS, Trade, TradeRules, generate_trades, match_trade_ads
from .http import HttpError
from .models import Inventory, ItemInfo
from .outlook import Outlook

# Demand floor for items we suggest hunting on the open market (2 = Normal).
MARKET_MIN_DEMAND = 2
GROWTH_TOP = 5


@dataclass
class Suggestions:
    ads_scanned: int
    ad_trades: list[Trade]  # trades someone on Rolimons is asking for right now
    market_trades: dict[str, list[Trade]] | None  # fallback when no ad matches
    growth_trades: list[Trade]  # the trades likeliest to gain value over time
    has_momentum: bool  # whether saved RAP history fed into the growth outlook


def suggest(me: Inventory, catalog: dict[int, ItemInfo], rules: TradeRules, top: int = 10,
            min_market_demand: int = MARKET_MIN_DEMAND) -> Suggestions:
    try:
        ads = tradeads.fetch_recent_ads(catalog)
    except (HttpError, RuntimeError) as e:
        print(f"Could not load Rolimons trade ads ({e}); showing market trades instead.", file=sys.stderr)
        ads = []

    outlook = Outlook(history.record_and_get_momentum(catalog))
    market_rules = dataclasses.replace(rules, min_receive_demand=min_market_demand, per_kind=top)

    ad_trades = match_trade_ads(me.items, ads, rules, limit=top)
    market = None if ad_trades else generate_trades(me.items, list(catalog.values()), market_rules)

    # Same sources, re-searched for growth instead of immediate value.
    if ad_trades:
        growth = match_trade_ads(me.items, ads, rules, limit=top, rank=outlook.rank)
    else:
        by_kind = generate_trades(me.items, list(catalog.values()), market_rules, rank=outlook.rank)
        growth = sorted((t for k in KINDS for t in by_kind[k]), key=outlook.rank, reverse=True)

    for t in ad_trades + growth + [t for k in KINDS for t in (market or {}).get(k, [])]:
        t.growth = outlook.trade(t)
    growth = [t for t in growth if t.growth > 0][:GROWTH_TOP]
    return Suggestions(len(ads), ad_trades, market, growth, bool(outlook.momentum))
