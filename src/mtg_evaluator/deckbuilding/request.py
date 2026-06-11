"""
DeckRequest — the fully-resolved set of user preferences before card pool generation.

Populated by the intake flow (CLI, API, or chat). Once complete, passed to
build_card_pool() to produce a ranked card pool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Bracket tags that are LEGAL at each bracket level
# (decks at bracket N may include combos tagged at or below their tier)
BRACKET_COMBO_ALLOWANCE: dict[int, set[str]] = {
    1: {"E", "C", "O"},  # Exhibition: casual combos only
    2: {"E", "C", "O"},  # Core: same
    3: {
        "E",
        "C",
        "O",
        "P",
        "S",
    },  # Upgraded: up to Powerful/Spicy (≤3 GC limit still applies)
    4: {"E", "C", "O", "P", "S", "R"},  # Optimized: all combos
    5: {"E", "C", "O", "P", "S", "R"},  # cEDH: all combos
}

TutorDensity = Literal["none", "light", "heavy"]
SaltTolerance = Literal["any", "low", "medium", "high"]

# Max salt score allowed per tolerance level (EDHREC salt is 0-4).
# "any" = no filtering. "low" = only mild cards. "high" = almost everything.
SALT_THRESHOLDS: dict[str, float] = {
    "low": 1.0,
    "medium": 2.0,
    "high": 3.0,
}


@dataclass
class DeckRequest:
    """Fully resolved user preferences for a deck build."""

    commander_name: str
    partner_name: str | None = None

    bracket: int = 3  # 1-5
    archetype: str | None = None  # "midrange", "tokens", "sacrifice", etc.
    want_combos: bool = True
    allowed_combo_tags: set[str] = field(default_factory=set)  # derived from bracket
    tutor_density: TutorDensity = "light"
    max_card_price: float | None = None  # USD cap per card; None = no budget limit
    salt_tolerance: SaltTolerance = "any"  # filter cards above the salt threshold
    free_text_notes: str | None = None  # any extra user notes

    pool_size: int = 300  # how many cards to return

    def __post_init__(self) -> None:
        if not self.allowed_combo_tags:
            self.allowed_combo_tags = BRACKET_COMBO_ALLOWANCE.get(self.bracket, set())
        if not self.want_combos:
            self.allowed_combo_tags = set()

    @property
    def bracket_label(self) -> str:
        return {1: "B1", 2: "B2", 3: "B3", 4: "B4", 5: "B5/cEDH"}[self.bracket]
