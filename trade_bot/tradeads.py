"""Rolimons trade ads: what players are currently offering and asking for."""

from __future__ import annotations

from dataclasses import dataclass, field

from .http import request_json
from .models import ItemInfo

RECENT_ADS_URL = "https://api.rolimons.com/tradeads/v1/getrecentads"

# The API sends request tags as numbers, in the order the Rolimons trade ad form lists them.
TAG_NAMES = {
    1: "demand",
    2: "rares",
    3: "robux",
    4: "any",
    5: "upgrade",
    6: "downgrade",
    7: "rap",
    8: "wishlist",
    9: "projecteds",
    10: "adds",
}


def tag_name(tag) -> str:
    if isinstance(tag, int) or (isinstance(tag, str) and tag.isdigit()):
        return TAG_NAMES.get(int(tag), f"tag{tag}")
    return str(tag).lower()


@dataclass
class TradeAd:
    ad_id: int
    created: int  # unix timestamp
    user_id: int
    username: str
    offer_items: list[ItemInfo]
    offer_robux: int = 0
    request_items: list[ItemInfo] = field(default_factory=list)
    request_tags: list[str] = field(default_factory=list)

    @property
    def trade_url(self) -> str:
        return f"https://www.roblox.com/users/{self.user_id}/trade"

    @property
    def profile_url(self) -> str:
        return f"https://www.rolimons.com/player/{self.user_id}"


def _side(raw) -> dict:
    return raw if isinstance(raw, dict) else {}


def parse_trade_ads(payload: dict, catalog: dict[int, ItemInfo]) -> list[TradeAd]:
    """Parse getrecentads.

    Each ad is [ad_id, created, user_id, username, offer, request] where
    offer = {"items": [asset ids], "robux": n} and
    request = {"items": [asset ids], "tags": ["upgrade", "any", ...]}.
    Ads that mention an item Rolimons doesn't value are skipped.
    """
    if not payload.get("success", True):
        raise RuntimeError("Rolimons trade ads request was not successful")
    ads = []
    for row in payload.get("trade_ads") or []:
        try:
            ad_id, created, user_id, username, offer, request = row[:6]
            offer, request = _side(offer), _side(request)
            offer_ids = [int(a) for a in offer.get("items") or []]
            request_ids = [int(a) for a in request.get("items") or []]
        except (TypeError, ValueError):
            continue  # malformed ad
        if any(a not in catalog for a in offer_ids + request_ids):
            continue
        ads.append(TradeAd(
            ad_id=int(ad_id),
            created=int(created),
            user_id=int(user_id),
            username=str(username),
            offer_items=[catalog[a] for a in offer_ids],
            offer_robux=int(offer.get("robux") or 0),
            request_items=[catalog[a] for a in request_ids],
            request_tags=[tag_name(t) for t in request.get("tags") or []],
        ))
    return ads


def fetch_recent_ads(catalog: dict[int, ItemInfo]) -> list[TradeAd]:
    return parse_trade_ads(request_json(RECENT_ADS_URL), catalog)
