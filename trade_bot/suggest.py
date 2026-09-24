"""The main flow: live trade ads first, general market suggestions as a fallback."""

from __future__ import annotations

import dataclasses
import sys
from dataclasses import dataclass

from . import tradeads
from .engine import Trade, TradeRules, generate_trades, match_trade_ads
from .http import HttpError
from .models import Inventory, ItemInfo

# Demand floor for items we suggest hunting on the open market (2 = Normal).
MARKET_MIN_DEMAND = 2


@dataclass
class Suggestions:
    ads_scanned: int
    ad_trades: list[Trade]  # trades someone on Rolimons is asking for right now
    market_trades: dict[str, list[Trade]] | None  # fallback when no ad matches


def suggest(me: Inventory, catalog: dict[int, ItemInfo], rules: TradeRules, top: int = 10,
            min_market_demand: int = MARKET_MIN_DEMAND) -> Suggestions:
    try:
        ads = tradeads.fetch_recent_ads(catalog)
    except (HttpError, RuntimeError) as e:
        print(f"Could not load Rolimons trade ads ({e}); showing market trades instead.", file=sys.stderr)
        ads = []

    ad_trades = match_trade_ads(me.items, ads, rules, limit=top)
    market = None
    if not ad_trades:
        market_rules = dataclasses.replace(rules, min_receive_demand=min_market_demand, per_kind=top)
        market = generate_trades(me.items, list(catalog.values()), market_rules)
    return Suggestions(len(ads), ad_trades, market)
