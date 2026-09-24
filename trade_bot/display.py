"""Plain-text output shared by the CLI and the interactive app."""

from __future__ import annotations

import sys
import time

from .engine import Trade
from .models import DEMAND_LABELS, TREND_LABELS, Inventory, ItemInfo
from .outlook import outlook_label


def safe_console() -> None:
    """Item names can contain characters an old Windows code page can't print."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


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


def ago(timestamp: int) -> str:
    seconds = max(0, int(time.time()) - timestamp)
    if seconds < 90:
        return "just now"
    if seconds < 90 * 60:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


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
    if t.ad:
        wants = "your exact items" if t.ad.request_items else ", ".join(t.ad.request_tags)
        print(f"      Trade ad by {t.ad.username} ({ago(t.ad.created)}), asking for: {wants}")
    if t.growth is not None:
        print(f"      Growth outlook: {outlook_label(t.growth)} ({t.growth:+.2f})")
    print(f"      GIVE    {fmt(t.give_value):>12}")
    for i in t.give:
        print(f"        - {describe(i)}")
    print(f"      RECEIVE {fmt(t.receive_value):>12}")
    for i in t.receive:
        print(f"        + {describe(i)}")
    if t.receive_robux:
        print(f"        + {fmt(t.receive_robux)} Robux ({fmt(t.robux_value)} after Roblox's 30% tax)")
    if t.ad:
        print(f"      Send it: {t.ad.trade_url}")


def print_section(title: str, trades: list[Trade], empty: str = "none found with the current settings") -> None:
    print(f"\n=== {title} ({len(trades)}) ===")
    if not trades:
        print(f"  {empty}")
    for n, t in enumerate(trades, 1):
        print_trade(n, t)
