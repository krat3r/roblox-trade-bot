"""Local RAP history, so growth predictions can use real price momentum.

Rolimons' public API has no history, so each day the bot runs it saves the
RAP of every item. Once it has a few days of data, the RAP change feeds
into the growth outlook.
"""

from __future__ import annotations

import datetime as dt
import json

from .models import ItemInfo
from .rolimons import CACHE_PATH

HISTORY_PATH = CACHE_PATH.with_name("rap_history.json")
KEEP_DAYS = 45
MIN_SPAN_DAYS = 3  # don't call a one-day blip momentum


def _load() -> dict[str, dict[str, int]]:
    try:
        return json.loads(HISTORY_PATH.read_text())
    except (OSError, ValueError):
        return {}


def record_and_get_momentum(catalog: dict[int, ItemInfo], today: dt.date | None = None) -> dict[int, float]:
    """Save today's RAPs; return {asset_id: fractional RAP change since the oldest saved day}."""
    today = today or dt.date.today()
    snapshots = _load()
    snapshots[today.isoformat()] = {str(a): i.rap for a, i in catalog.items() if i.rap > 0}
    cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    snapshots = {day: raps for day, raps in snapshots.items() if day >= cutoff}
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_PATH.write_text(json.dumps(snapshots))
    except OSError:
        pass  # history is best-effort

    oldest = min(snapshots)
    if (today - dt.date.fromisoformat(oldest)).days < MIN_SPAN_DAYS:
        return {}
    momentum = {}
    for asset_id, old_rap in snapshots[oldest].items():
        item = catalog.get(int(asset_id))
        if item and old_rap > 0 and item.rap > 0:
            momentum[item.asset_id] = (item.rap - old_rap) / old_rap
    return momentum
