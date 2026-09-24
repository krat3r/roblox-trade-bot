"""Rolimons API client: item values and player inventory scans."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .http import HttpError, request_json
from .models import ItemInfo

ITEM_DETAILS_URLS = (
    "https://api.rolimons.com/items/v2/itemdetails",
    "https://www.rolimons.com/itemapi/itemdetails",
)
PLAYER_ASSETS_URL = "https://api.rolimons.com/players/v1/playerassets/{user_id}"

# Rolimons asks clients not to hammer the item endpoint; cache it locally.
CACHE_PATH = Path(os.environ.get("TRADE_BOT_CACHE", Path.home() / ".cache" / "roblox-trade-bot" / "items.json"))
CACHE_TTL_SECONDS = 5 * 60


def parse_item_details(payload: dict) -> dict[int, ItemInfo]:
    """Parse the itemdetails response.

    Each item is a list: [name, acronym, rap, value, default_value,
    demand, trend, projected, hyped, rare]. Missing values are -1.
    """
    if not payload.get("success", True):
        raise RuntimeError("Rolimons itemdetails request was not successful")
    items: dict[int, ItemInfo] = {}
    for asset_id, row in payload["items"].items():
        name, acronym, rap, value, _default, demand, trend, projected, hyped, rare = row[:10]
        items[int(asset_id)] = ItemInfo(
            asset_id=int(asset_id),
            name=name,
            acronym=acronym or "",
            rap=max(int(rap), 0),
            value=int(value),
            demand=int(demand),
            trend=int(trend),
            projected=projected == 1,
            hyped=hyped == 1,
            rare=rare == 1,
        )
    return items


def fetch_item_details(use_cache: bool = True) -> dict[int, ItemInfo]:
    if use_cache and CACHE_PATH.exists() and time.time() - CACHE_PATH.stat().st_mtime < CACHE_TTL_SECONDS:
        return parse_item_details(json.loads(CACHE_PATH.read_text()))

    last_error: Exception | None = None
    for url in ITEM_DETAILS_URLS:
        try:
            payload = request_json(url)
            break
        except HttpError as e:
            last_error = e
    else:
        raise RuntimeError(f"Could not load Rolimons item values: {last_error}")

    items = parse_item_details(payload)
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(payload))
    except OSError:
        pass  # caching is best-effort
    return items


class PrivateInventoryError(RuntimeError):
    pass


def fetch_player_assets(user_id: int) -> tuple[dict[int, list[int]], set[int]]:
    """Return ({asset_id: [uaid, ...]}, {uaids on trade hold}) from a Rolimons scan."""
    # Rolimons scans big inventories live on first request, which can take a while.
    payload = request_json(PLAYER_ASSETS_URL.format(user_id=user_id), timeout=90)
    if not payload.get("success", False):
        raise RuntimeError(f"Rolimons could not scan player {user_id}")
    if payload.get("playerPrivacyEnabled"):
        raise PrivateInventoryError(f"Player {user_id} has a private inventory")
    assets = {int(aid): [int(u) for u in uaids] for aid, uaids in (payload.get("playerAssets") or {}).items()}
    holds = {int(u) for u in payload.get("holds") or []}
    return assets, holds
