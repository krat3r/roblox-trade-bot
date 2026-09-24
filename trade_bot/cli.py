"""Command line interface.

    python -m trade_bot                          # interactive: asks for a username
    python -m trade_bot <you>                    # live Rolimons trade ads, else best market trades
    python -m trade_bot <you> --partner <them>   # best trades using only their inventory
"""

from __future__ import annotations

import argparse
import json
import sys

from . import rolimons
from .display import safe_console, print_inventory, print_section
from .engine import KINDS, TradeRules, generate_trades
from .http import HttpError
from .models import ItemInfo
from .scanner import scan_player
from .suggest import MARKET_MIN_DEMAND, suggest


def resolve_keep(names: list[str], catalog: dict[int, ItemInfo]) -> set[int]:
    keep = set()
    for raw in names:
        if raw.isdigit():
            keep.add(int(raw))
            continue
        needle = raw.lower()
        matches = [i.asset_id for i in catalog.values() if needle in (i.name.lower(), i.acronym.lower())]
        if not matches:
            sys.exit(f"--keep: no item named {raw!r}")
        keep.update(matches)
    return keep


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="trade_bot",
        description="Scan a Roblox inventory and suggest the best trades using Rolimons values.",
    )
    p.add_argument("player", nargs="?", help="your Roblox username or user ID (omit to be asked)")
    p.add_argument("--partner", help="trade partner's username or ID (default: use the whole Rolimons market)")
    p.add_argument("--kind", choices=KINDS, action="append", help="only show this trade kind (repeatable)")
    p.add_argument("--top", type=int, default=10, help="trades to show per kind (default 10)")
    p.add_argument("--max-give", type=int, default=4, help="max items you give in an upgrade (default 4)")
    p.add_argument("--max-receive", type=int, default=4, help="max items you receive in a downgrade (default 4)")
    p.add_argument("--min-demand", type=int, choices=range(-1, 5),
                   help="lowest demand you'll accept, -1..4 (default: 2 for market suggestions, otherwise -1)")
    p.add_argument("--min-value", type=int, default=0, help="ignore items worth less than this")
    p.add_argument("--keep", action="append", default=[], metavar="ITEM",
                   help="never trade this item away (name, acronym or asset ID; repeatable)")
    p.add_argument("--allow-projected", action="store_true", help="include items Rolimons flags as projected")
    p.add_argument("--allow-hyped", action="store_true", help="allow receiving items flagged as hyped")
    p.add_argument("--upgrade-ratio", type=float, nargs=2, metavar=("MIN", "MAX"), default=(0.80, 0.95),
                   help="receive/give value window for upgrades (default 0.80 0.95: you overpay 5-20%%)")
    p.add_argument("--downgrade-ratio", type=float, nargs=2, metavar=("MIN", "MAX"), default=(1.05, 1.20),
                   help="window for downgrades (default 1.05 1.20: they overpay 5-20%%)")
    p.add_argument("--sidegrade-ratio", type=float, nargs=2, metavar=("MIN", "MAX"), default=(0.97, 1.08),
                   help="window for 1-for-1 trades (default 0.97 1.08)")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON")
    p.add_argument("--no-cache", action="store_true", help="always re-download Rolimons values")
    return p.parse_args(argv)


def build_rules(args, catalog: dict[int, ItemInfo]) -> TradeRules:
    return TradeRules(
        upgrade_ratio=tuple(args.upgrade_ratio),
        downgrade_ratio=tuple(args.downgrade_ratio),
        sidegrade_ratio=tuple(args.sidegrade_ratio),
        max_give=args.max_give,
        max_receive=args.max_receive,
        min_receive_demand=-1 if args.min_demand is None else args.min_demand,
        min_item_value=args.min_value,
        allow_projected=args.allow_projected,
        allow_hyped=args.allow_hyped,
        keep=resolve_keep(args.keep, catalog),
        per_kind=args.top,
    )


def main(argv: list[str] | None = None) -> int:
    safe_console()
    args = parse_args(argv)
    if not args.player:
        from .app import main as interactive_main

        return interactive_main()

    try:
        catalog = rolimons.fetch_item_details(use_cache=not args.no_cache)
        me = scan_player(args.player, catalog)
        partner = scan_player(args.partner, catalog) if args.partner else None
    except (HttpError, RuntimeError, ValueError) as e:
        sys.exit(f"error: {e}")
    rules = build_rules(args, catalog)
    kinds = args.kind or list(KINDS)
    player = {"id": me.user_id, "name": me.username, "total_value": me.total_value}

    if partner:
        results = generate_trades(me.items, [o.info for o in partner.items if not o.on_hold], rules)
        if args.json:
            print(json.dumps({
                "player": player,
                "partner": {"id": partner.user_id, "name": partner.username},
                "trades": {k: [t.to_dict() for t in results[k]] for k in kinds},
            }, indent=2))
            return 0
        print_inventory(me)
        print_inventory(partner)
        for kind in kinds:
            print_section(f"Best {kind}s from {partner.username}'s inventory", results[kind])
        print()
        return 0

    market_demand = MARKET_MIN_DEMAND if args.min_demand is None else args.min_demand
    found = suggest(me, catalog, rules, top=args.top, min_market_demand=market_demand)
    if args.json:
        print(json.dumps({
            "player": player,
            "ads_scanned": found.ads_scanned,
            "ad_trades": [t.to_dict() for t in found.ad_trades],
            "market_trades": {k: [t.to_dict() for t in found.market_trades[k]] for k in kinds}
            if found.market_trades else None,
        }, indent=2))
        return 0
    print_inventory(me)
    print_suggestions(found, kinds)
    print()
    return 0


def print_suggestions(found, kinds=KINDS) -> None:
    if found.ad_trades:
        print_section(f"Players on Rolimons who want a trade you can do now "
                      f"({found.ads_scanned} ads scanned)", found.ad_trades)
        return
    print(f"\nNobody in the {found.ads_scanned} latest Rolimons trade ads wants a trade you can do right now.")
    print("Here are the best trades to look for instead:")
    for kind in kinds:
        print_section(f"Best {kind}s on the Rolimons market", found.market_trades[kind])
