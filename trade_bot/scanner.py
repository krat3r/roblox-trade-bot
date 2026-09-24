"""Turns a username into a valued inventory."""

from __future__ import annotations

import sys

from . import roblox, rolimons
from .http import HttpError
from .models import Inventory, ItemInfo, OwnedItem


def build_inventory(
    user_id: int,
    username: str,
    assets: dict[int, list[int]],
    holds: set[int],
    catalog: dict[int, ItemInfo],
) -> Inventory:
    inv = Inventory(user_id=user_id, username=username)
    for asset_id, uaids in assets.items():
        info = catalog.get(asset_id)
        if info is None:
            continue  # not a limited Rolimons tracks
        for uaid in uaids:
            inv.items.append(OwnedItem(info=info, uaid=uaid, on_hold=uaid in holds))
    inv.items.sort(key=lambda o: o.info.trade_value, reverse=True)
    return inv


def scan_player(name_or_id: str, catalog: dict[int, ItemInfo]) -> Inventory:
    user_id, username = roblox.resolve_user(name_or_id)
    try:
        assets, holds = rolimons.fetch_player_assets(user_id)
    except rolimons.PrivateInventoryError:
        raise
    except (HttpError, RuntimeError) as e:
        print(f"Rolimons scan failed ({e}); falling back to the Roblox inventory API.", file=sys.stderr)
        assets, holds = roblox.fetch_collectibles(user_id)
    return build_inventory(user_id, username, assets, holds, catalog)
