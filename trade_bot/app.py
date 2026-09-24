"""Interactive mode: what runs when you double-click the .exe or the .py launcher."""

from __future__ import annotations

import sys
import traceback

from . import rolimons
from .cli import print_suggestions
from .display import safe_console, fmt, print_inventory
from .engine import TradeRules
from .http import HttpError
from .scanner import scan_player
from .suggest import suggest

BANNER = r"""
 ============================================
   Rolimons Trade Bot
   Finds the best trades for any Roblox player
 ============================================
"""


def ask(prompt: str) -> str | None:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return None


def scan_once(username: str, catalog) -> None:
    print(f"\nScanning {username}'s inventory on Rolimons (big inventories can take up to a minute)...")
    me = scan_player(username, catalog)
    if not me.items:
        print(f"{me.username} has no tradeable limiteds.")
        return
    print_inventory(me, limit=10)
    print("\nChecking the latest Rolimons trade ads...")
    found = suggest(me, catalog, TradeRules())
    print_suggestions(found)
    print(f"\nDone. {me.username}'s inventory is worth {fmt(me.total_value)} in total.")


def main() -> int:
    safe_console()
    print(BANNER)
    print("Loading item values from Rolimons...")
    try:
        catalog = rolimons.fetch_item_details()
    except (HttpError, RuntimeError) as e:
        print(f"\nCould not reach Rolimons: {e}")
        print("Check your internet connection and try again.")
        ask("\nPress Enter to close...")
        return 1
    print(f"Loaded {fmt(len(catalog))} items.")

    while True:
        username = ask("\nEnter a Roblox username (or press Enter to quit): ")
        if not username:
            return 0
        try:
            scan_once(username, catalog)
        except rolimons.PrivateInventoryError:
            print(f"\n{username}'s inventory is private, so it can't be scanned.")
        except ValueError as e:
            print(f"\n{e}")
        except (HttpError, RuntimeError) as e:
            print(f"\nSomething went wrong talking to Roblox or Rolimons: {e}")
        except Exception:
            # Keep the window open so the error can be read and reported.
            traceback.print_exc()
            print("\nUnexpected error. Please report the text above.")


if __name__ == "__main__":
    sys.exit(main())
