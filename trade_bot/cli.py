"""Command line interface.

    python -m trade_bot <you>                    # best trades vs. the whole Rolimons market
    python -m trade_bot <you> --partner <them>   # best trades using only their inventory
"""

from __future__ import annotations

import argparse
import json
import sys

from . import rolimons
from .http import HttpError
from .engine import KINDS, Trade, TradeRules, generate_trades
from .models import DEMAND_LABELS, TREND_LABELS, Inventory, ItemInfo
from .scanner import scan_player


def fmt(n: int) -> str:
    return f"{n:,}"


def describe(item: ItemInfo) -> str:
    tags = [DEMAND_LABELS.get(item.demand, "?") + " demand"]
    if item.trend in (0, 3):
        tags.append(TREND_LABELS[item.trend].lower())
    if not item.has_value:
        tags.append("RAP only")
    if item.rare:
        tags.append("rare")
    return f"{item.name} ({fmt(item.trade_value)}; {', '.join(tags)})"


def print_inventory(inv: Inventory, limit: int = 15) -> None:
    held = sum(1 for o in inv.items if o.on_hold)
    print(f"\n{inv.username} ({inv.user_id}): {len(inv.items)} limiteds, "
          f"total value {fmt(inv.total_value)}" + (f", {held} on trade hold" if held else ""))
    for o in inv.items[:limit]:
        print(f"  - {describe(o.info)}" + ("  [on hold]" if o.on_hold else ""))
    if len(inv.items) > limit:
        print(f"  ... and {len(inv.items) - limit} more")


def print_trade(n: int, t: Trade) -> None:
    sign = "+" if t.gain >= 0 else ""
    print(f"\n  #{n}  you {sign}{fmt(t.gain)} ({(t.ratio - 1) * 100:+.1f}%)   score {t.score:.3f}")
    print(f"      GIVE    {fmt(t.give_value):>12}")
    for i in t.give:
        print(f"        - {describe(i)}")
    print(f"      RECEIVE {fmt(t.receive_value):>12}")
    for i in t.receive:
        print(f"        + {describe(i)}")


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
    p.add_argument("player", help="your Roblox username or user ID")
    p.add_argument("--partner", help="trade partner's username or ID (default: use the whole Rolimons market)")
    p.add_argument("--kind", choices=KINDS, action="append", help="only show this trade kind (repeatable)")
    p.add_argument("--top", type=int, default=10, help="trades to show per kind (default 10)")
    p.add_argument("--max-give", type=int, default=4, help="max items you give in an upgrade (default 4)")
    p.add_argument("--max-receive", type=int, default=4, help="max items you receive in a downgrade (default 4)")
    p.add_argument("--min-demand", type=int, choices=range(-1, 5),
                   help="lowest demand you'll accept, -1..4 (default: 2 for market mode, -1 with --partner)")
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        catalog = rolimons.fetch_item_details(use_cache=not args.no_cache)
        me = scan_player(args.player, catalog)
        partner = scan_player(args.partner, catalog) if args.partner else None
    except (HttpError, RuntimeError, ValueError) as e:
        sys.exit(f"error: {e}")

    if partner:
        their_items = [o.info for o in partner.items if not o.on_hold]
        min_demand = -1 if args.min_demand is None else args.min_demand
    else:
        their_items = list(catalog.values())
        min_demand = 2 if args.min_demand is None else args.min_demand

    rules = TradeRules(
        upgrade_ratio=tuple(args.upgrade_ratio),
        downgrade_ratio=tuple(args.downgrade_ratio),
        sidegrade_ratio=tuple(args.sidegrade_ratio),
        max_give=args.max_give,
        max_receive=args.max_receive,
        min_receive_demand=min_demand,
        min_item_value=args.min_value,
        allow_projected=args.allow_projected,
        allow_hyped=args.allow_hyped,
        keep=resolve_keep(args.keep, catalog),
        per_kind=args.top,
    )
    results = generate_trades(me.items, their_items, rules)
    kinds = args.kind or list(KINDS)

    if args.json:
        out = {
            "player": {"id": me.user_id, "name": me.username, "total_value": me.total_value},
            "partner": {"id": partner.user_id, "name": partner.username} if partner else None,
            "trades": {k: [t.to_dict() for t in results[k]] for k in kinds},
        }
        print(json.dumps(out, indent=2))
        return 0

    print_inventory(me)
    if partner:
        print_inventory(partner)
    source = f"{partner.username}'s inventory" if partner else "the Rolimons market"
    for kind in kinds:
        trades = results[kind]
        print(f"\n=== Best {kind}s from {source} ({len(trades)}) ===")
        if not trades:
            print("  none found with the current settings")
        for n, t in enumerate(trades, 1):
            print_trade(n, t)
    print()
    return 0
