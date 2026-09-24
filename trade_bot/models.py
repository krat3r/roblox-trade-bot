"""Data models shared by the API clients and the trade engine."""

from __future__ import annotations

from dataclasses import dataclass, field

DEMAND_LABELS = {
    -1: "None",
    0: "Terrible",
    1: "Low",
    2: "Normal",
    3: "High",
    4: "Amazing",
}

TREND_LABELS = {
    -1: "None",
    0: "Lowering",
    1: "Unstable",
    2: "Stable",
    3: "Raising",
    4: "Fluctuating",
}


@dataclass(frozen=True)
class ItemInfo:
    """Market data for one limited item, as published by Rolimons."""

    asset_id: int
    name: str
    acronym: str
    rap: int
    value: int  # Rolimons value, or -1 when the item has none
    demand: int
    trend: int
    projected: bool
    hyped: bool
    rare: bool

    @property
    def has_value(self) -> bool:
        return self.value > 0

    @property
    def trade_value(self) -> int:
        """The number traders use: Rolimons value if assigned, otherwise RAP."""
        return self.value if self.has_value else self.rap

    @property
    def label(self) -> str:
        return self.acronym or self.name


@dataclass(frozen=True)
class OwnedItem:
    """One specific copy of an item in a player's inventory."""

    info: ItemInfo
    uaid: int | None = None
    on_hold: bool = False


@dataclass
class Inventory:
    user_id: int
    username: str
    items: list[OwnedItem] = field(default_factory=list)

    @property
    def total_value(self) -> int:
        return sum(i.info.trade_value for i in self.items)
