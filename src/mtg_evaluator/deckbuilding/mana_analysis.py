"""
Mana base reliability analysis for Commander decks.

Evaluates colored source availability using Karsten-derived thresholds for
99-card Commander decks, detects pip pressure on the commander, and flags
mana base issues that would hurt consistency.

Reference: Frank Karsten's updated Commander mana base analysis.
Key insight: for a spell with N pips of color C at CMC M, you need approximately
  (N pips, turn M) → required sources from Karsten lookup table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Optional

from mtg_evaluator.deckbuilding.lands import LAND_SCORES

# Karsten-approximate thresholds for 99-card Commander.
# Outer key = number of pips. Inner key = turn you want to cast it.
# Value = minimum colored sources needed to hit ~90% reliability.
_KARSTEN_SOURCES: dict[int, dict[int, int]] = {
    1: {1: 14, 2: 12, 3: 10, 4: 9, 5: 8, 6: 7},
    2: {2: 20, 3: 17, 4: 15, 5: 13, 6: 12},
    3: {3: 23, 4: 20, 5: 18, 6: 16},
    4: {4: 24, 5: 22, 6: 20},
    5: {5: 24, 6: 23},
}

_PIP_RE = re.compile(r"\{([WUBRG])\}", re.IGNORECASE)
_MANA_SYMBOL_RE = re.compile(r"\{([WUBRGC])\}", re.IGNORECASE)
_GENERIC_MANA_RE = re.compile(r"\{(\d+|X|Y|Z|C|S|P)\}", re.IGNORECASE)
_ANY_COLOR_RE = re.compile(
    r"add (one |two |three |any amount of )?mana (of|in) any "
    r"(color|combination of colors)",
    re.IGNORECASE,
)
_ADD_SENTENCE_RE = re.compile(r"[^.]*\badd\b[^.]*", re.IGNORECASE)


def count_pips(mana_cost: Optional[str]) -> dict[str, int]:
    """Count colored pips per color in a mana cost string like '{2}{W}{B}{B}'."""
    if not mana_cost:
        return {}
    pips: dict[str, int] = {}
    for sym in _PIP_RE.findall(mana_cost):
        color = sym.upper()
        pips[color] = pips.get(color, 0) + 1
    return pips


def required_sources(pip_count: int, turn: int) -> int:
    """
    Return the number of on-color sources needed to cast a spell with pip_count
    pips of a color by the given turn, per Karsten's table.
    Returns 0 if pip_count is 0 or the combination is not in the table.
    """
    if pip_count <= 0:
        return 0
    pip_count = min(pip_count, 5)
    turn = max(1, min(turn, 6))
    inner = _KARSTEN_SOURCES.get(pip_count, {})
    # Find the closest turn key >= requested turn
    for t in sorted(inner.keys()):
        if t >= turn:
            return inner[t]
    # Fall back to highest turn in table
    return max(inner.values()) if inner else 0


# ---------------------------------------------------------------------------
# Land color source counting
# ---------------------------------------------------------------------------

# For our known dual/fetch/shock lands, assign which colors they produce.
# This covers the most common lands. Unknown lands are treated as unknown unless
# Scryfall produced_mana or oracle text is provided by the caller.
_LAND_COLORS: dict[str, set[str]] = {
    # Original duals
    "Underground Sea": {"U", "B"},
    "Tundra": {"W", "U"},
    "Volcanic Island": {"U", "R"},
    "Badlands": {"B", "R"},
    "Taiga": {"R", "G"},
    "Savannah": {"W", "G"},
    "Scrubland": {"W", "B"},
    "Bayou": {"B", "G"},
    "Tropical Island": {"G", "U"},
    "Plateau": {"R", "W"},
    # Shocklands
    "Watery Grave": {"U", "B"},
    "Hallowed Fountain": {"W", "U"},
    "Blood Crypt": {"B", "R"},
    "Stomping Ground": {"R", "G"},
    "Temple Garden": {"W", "G"},
    "Godless Shrine": {"W", "B"},
    "Steam Vents": {"U", "R"},
    "Overgrown Tomb": {"B", "G"},
    "Sacred Foundry": {"R", "W"},
    "Breeding Pool": {"G", "U"},
    # Fetchlands (can produce any color depending on what they fetch)
    "Polluted Delta": {"U", "B"},
    "Flooded Strand": {"W", "U"},
    "Bloodstained Mire": {"B", "R"},
    "Wooded Foothills": {"R", "G"},
    "Windswept Heath": {"W", "G"},
    "Scalding Tarn": {"U", "R"},
    "Verdant Catacombs": {"B", "G"},
    "Arid Mesa": {"R", "W"},
    "Misty Rainforest": {"G", "U"},
    "Marsh Flats": {"W", "B"},
    "Prismatic Vista": {"W", "U", "B", "R", "G"},
    # Any-color
    "Command Tower": None,  # produces all colors in identity
    "Mana Confluence": None,
    "City of Brass": None,
    "Reflecting Pool": None,
    "Forbidden Orchard": None,
    "Exotic Orchard": None,
    # Pain lands
    "Underground River": {"U", "B"},
    "Adarkar Wastes": {"W", "U"},
    "Shivan Reef": {"U", "R"},
    "Sulfurous Springs": {"B", "R"},
    "Karplusan Forest": {"R", "G"},
    "Brushland": {"W", "G"},
    "Caves of Koilos": {"W", "B"},
    "Battlefield Forge": {"R", "W"},
    "Llanowar Wastes": {"B", "G"},
    "Yavimaya Coast": {"G", "U"},
    # Check lands (same colors as corresponding shock/dual)
    "Drowned Catacomb": {"U", "B"},
    "Glacial Fortress": {"W", "U"},
    "Sulfur Falls": {"U", "R"},
    "Dragonskull Summit": {"B", "R"},
    "Rootbound Crag": {"R", "G"},
    "Sunpetal Grove": {"W", "G"},
    "Isolated Chapel": {"W", "B"},
    "Woodland Cemetery": {"B", "G"},
    "Hinterland Harbor": {"G", "U"},
    "Clifftop Retreat": {"R", "W"},
    # Basics
    "Plains": {"W"},
    "Island": {"U"},
    "Swamp": {"B"},
    "Mountain": {"R"},
    "Forest": {"G"},
    "Wastes": set(),
    "Snow-Covered Plains": {"W"},
    "Snow-Covered Island": {"U"},
    "Snow-Covered Swamp": {"B"},
    "Snow-Covered Mountain": {"R"},
    "Snow-Covered Forest": {"G"},
}


@dataclass(frozen=True)
class LandManaData:
    """Known mana-production details for a land."""

    produced_mana: tuple[str, ...] = ()
    oracle_text: str = ""


def _identity_colors(deck_color_identity: list[str]) -> set[str]:
    return set(deck_color_identity) - {"C"}


def _colors_from_produced_mana(
    produced_mana: tuple[str, ...] | list[str] | None,
    deck_color_identity: list[str],
) -> set[str] | None:
    if not produced_mana:
        return None
    allowed = set(deck_color_identity) | {"C"}
    return {
        str(color).upper() for color in produced_mana if str(color).upper() in allowed
    }


def _colors_from_oracle_text(
    oracle_text: str | None,
    deck_color_identity: list[str],
) -> set[str] | None:
    if not oracle_text:
        return None
    colors: set[str] = set()
    for sentence in _ADD_SENTENCE_RE.findall(oracle_text):
        if _ANY_COLOR_RE.search(sentence):
            return _identity_colors(deck_color_identity)
        colors.update(sym.upper() for sym in _MANA_SYMBOL_RE.findall(sentence))
    if colors:
        return colors & (set(deck_color_identity) | {"C"})
    return None


def land_colors(
    land_name: str,
    deck_color_identity: list[str],
    produced_mana: tuple[str, ...] | list[str] | None = None,
    oracle_text: str | None = None,
) -> set[str]:
    """Return the colors a land produces given the deck's color identity."""
    produced = _colors_from_produced_mana(produced_mana, deck_color_identity)
    if produced is not None:
        return produced

    if land_name in _LAND_COLORS:
        result = _LAND_COLORS[land_name]
        if result is None:
            # All-colors land: produces only colors in identity
            return _identity_colors(deck_color_identity)
        return result & (set(deck_color_identity) | {"C"})
    parsed = _colors_from_oracle_text(oracle_text, deck_color_identity)
    if parsed is not None:
        return parsed

    return set()


@dataclass
class ManaAnalysis:
    """Mana base reliability report."""

    color_sources: dict[str, int]  # color → count of sources
    pip_requirements: dict[str, int]  # color → pips needed for commander
    required_sources: dict[str, int]  # color → sources needed per Karsten
    color_deficits: dict[str, int]  # color → how many sources short
    land_count: int
    untapped_land_count: int  # T1 + T2 lands (roughly untapped)
    tapland_count: int  # T4 + T5 lands
    fetch_count: int
    unknown_land_count: int
    pip_stress: float  # 0-5: how demanding the commander's pips are
    cast_reliability: float  # 0-1: estimated commander cast reliability
    unknown_lands: list[str] = field(default_factory=list)
    pip_reliability: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "color_sources": self.color_sources,
            "pip_requirements": self.pip_requirements,
            "required_sources": self.required_sources,
            "color_deficits": self.color_deficits,
            "land_count": self.land_count,
            "untapped_land_count": self.untapped_land_count,
            "tapland_count": self.tapland_count,
            "fetch_count": self.fetch_count,
            "unknown_land_count": self.unknown_land_count,
            "unknown_lands": self.unknown_lands,
            "pip_stress": self.pip_stress,
            "cast_reliability": self.cast_reliability,
            "pip_reliability": self.pip_reliability,
            "warnings": self.warnings,
        }


def analyze_mana_base(
    land_names: list[str],
    commander_mana_cost: Optional[str],
    commander_cmc: float,
    deck_color_identity: list[str],
    partner_mana_cost: Optional[str] = None,
    land_metadata: Mapping[str, LandManaData] | None = None,
) -> ManaAnalysis:
    """
    Analyze the mana base of a (proposed) deck for a given commander.

    land_names: list of land card names in the deck
    commander_mana_cost: e.g. '{2}{W}{B}'
    commander_cmc: float CMC (for Karsten turn calculation)
    """
    # Count sources per color
    color_sources: dict[str, int] = {c: 0 for c in deck_color_identity if c != "C"}
    untapped = 0
    taplands = 0
    fetches = 0
    unknown_lands: list[str] = []
    land_metadata = land_metadata or {}

    for name in land_names:
        metadata = land_metadata.get(name, LandManaData())
        produces = land_colors(
            name,
            deck_color_identity,
            produced_mana=metadata.produced_mana,
            oracle_text=metadata.oracle_text,
        )
        if not produces and name not in _LAND_COLORS and name not in unknown_lands:
            unknown_lands.append(name)
        for c in produces:
            if c in color_sources:
                color_sources[c] += 1

        # Quality tier for tapland assessment
        score = LAND_SCORES.get(name, 40.0)
        if score >= 70:
            untapped += 1
        elif score <= 40:
            taplands += 1
        # Count fetches (T1 score AND name pattern)
        if score >= 90 and any(
            x in name
            for x in [
                "Delta",
                "Strand",
                "Mire",
                "Foothills",
                "Heath",
                "Tarn",
                "Catacombs",
                "Mesa",
                "Rainforest",
                "Flats",
                "Vista",
            ]
        ):
            fetches += 1

    # Commander pip requirements
    combined_cost = (commander_mana_cost or "") + (partner_mana_cost or "")
    cmd_pips = count_pips(combined_cost)
    turn = max(1, int(commander_cmc))

    req_sources: dict[str, int] = {}
    for color, pip_count in cmd_pips.items():
        req_sources[color] = required_sources(pip_count, turn)

    # Deficits
    deficits = {
        color: max(0, req - color_sources.get(color, 0))
        for color, req in req_sources.items()
    }

    # Pip stress: 0 = single color / no heavy pips; 5 = quad-color commander with 3+ pips each
    n_colors_with_pips = len(cmd_pips)
    max_pips = max(cmd_pips.values()) if cmd_pips else 0
    pip_stress = min(5.0, n_colors_with_pips * 0.8 + max_pips * 0.6)

    # Cast reliability (simple heuristic: fraction of required sources met)
    pip_reliability: dict[str, float] = {}
    if not req_sources:
        cast_reliability = 1.0
    else:
        fractions = []
        for color, required in req_sources.items():
            if required <= 0:
                continue
            reliability = round(min(1.0, color_sources.get(color, 0) / required), 3)
            pip_reliability[color] = reliability
            fractions.append(reliability)
        cast_reliability = (
            round(sum(fractions) / len(fractions), 3) if fractions else 1.0
        )

    # Warnings
    warns = []
    for color, deficit in deficits.items():
        if deficit > 0:
            warns.append(
                f"Need {deficit} more {color} sources for commander "
                f"(have {color_sources.get(color, 0)}, need {req_sources[color]})"
            )
    if taplands > 10:
        warns.append(
            f"High tapland count ({taplands}): slows early game significantly."
        )
    if taplands > 6 and commander_cmc <= 2:
        warns.append(
            "Taplands problematic with a low-CMC commander that wants fast starts."
        )
    if fetches == 0 and len(deck_color_identity) >= 3:
        warns.append("No fetchlands in 3+ color deck: color fixing may be unreliable.")
    if unknown_lands:
        preview = ", ".join(unknown_lands[:5])
        extra = f" (+{len(unknown_lands) - 5} more)" if len(unknown_lands) > 5 else ""
        warns.append(
            f"Unknown land mana production treated as colorless: {preview}{extra}."
        )

    return ManaAnalysis(
        color_sources=color_sources,
        pip_requirements=cmd_pips,
        required_sources=req_sources,
        color_deficits=deficits,
        land_count=len(land_names),
        untapped_land_count=untapped,
        tapland_count=taplands,
        fetch_count=fetches,
        unknown_land_count=len(unknown_lands),
        pip_stress=round(pip_stress, 2),
        cast_reliability=cast_reliability,
        unknown_lands=unknown_lands,
        pip_reliability=pip_reliability,
        warnings=warns,
    )
