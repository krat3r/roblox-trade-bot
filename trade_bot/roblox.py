"""Roblox web API client: username lookup and a fallback inventory scan."""

from __future__ import annotations

from urllib.parse import quote

from .http import HttpError, request_json

USERNAMES_URL = "https://users.roblox.com/v1/usernames/users"
USER_URL = "https://users.roblox.com/v1/users/{user_id}"
COLLECTIBLES_URL = (
    "https://inventory.roblox.com/v1/users/{user_id}/assets/collectibles"
    "?sortOrder=Asc&limit=100&cursor={cursor}"
)


def resolve_user(name_or_id: str) -> tuple[int, str]:
    """Accept a username or numeric user ID; return (user_id, username)."""
    if name_or_id.isdigit():
        user_id = int(name_or_id)
        try:
            return user_id, request_json(USER_URL.format(user_id=user_id))["name"]
        except HttpError:
            return user_id, name_or_id
    data = request_json(USERNAMES_URL, body={"usernames": [name_or_id], "excludeBannedUsers": False})
    if not data.get("data"):
        raise ValueError(f"No Roblox user named {name_or_id!r}")
    user = data["data"][0]
    return int(user["id"]), user["name"]


def fetch_collectibles(user_id: int) -> tuple[dict[int, list[int]], set[int]]:
    """Scan limiteds straight from Roblox. Same return shape as the Rolimons scan."""
    assets: dict[int, list[int]] = {}
    holds: set[int] = set()
    cursor = ""
    while True:
        try:
            page = request_json(COLLECTIBLES_URL.format(user_id=user_id, cursor=quote(cursor)))
        except HttpError as e:
            if e.status == 403:
                from .rolimons import PrivateInventoryError

                raise PrivateInventoryError(f"Player {user_id} has a private inventory") from e
            raise
        for entry in page.get("data", []):
            assets.setdefault(int(entry["assetId"]), []).append(int(entry["userAssetId"]))
            if entry.get("isOnHold"):
                holds.add(int(entry["userAssetId"]))
        cursor = page.get("nextPageCursor") or ""
        if not cursor:
            return assets, holds
