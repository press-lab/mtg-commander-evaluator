"""Canonical card-function taxonomy helpers.

The classifier, deckbuilder, evaluator, and UI filters all traffic in short
function labels. Keep the canonical spelling and common aliases in one place so
old classifications and prompt variants don't quietly miss package or role
checks.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_NON_WORD = re.compile(r"[^a-z0-9]+")


CANONICAL_FUNCTIONS: frozenset[str] = frozenset(
    {
        "ramp",
        "draw",
        "card_selection",
        "removal",
        "creature_removal",
        "artifact_removal",
        "enchantment_removal",
        "board_wipe",
        "counterspell",
        "tutor",
        "recursion",
        "reanimation",
        "token_maker",
        "sacrifice_outlet",
        "sacrifice_payoff",
        "aristocrat_payoff",
        "graveyard_hate",
        "graveyard_enabler",
        "exile_enabler",
        "exile_payoff",
        "blink",
        "etb_payoff",
        "death_trigger_payoff",
        "protection",
        "finisher",
        "anthem",
        "stax",
        "combo_piece",
        "cost_reducer",
        "mana_sink",
        "discard_outlet",
        "impulse_draw",
        "extra_combat",
        "extra_turn",
        "land_ramp",
        "treasure_maker",
    }
)


FUNCTION_ALIASES: dict[str, str] = {
    # Broad role wording
    "card_advantage": "draw",
    "card_draw": "draw",
    "draw_cards": "draw",
    "draw_spell": "draw",
    "selection": "card_selection",
    "filtering": "card_selection",
    "counter": "counterspell",
    "countermagic": "counterspell",
    "permission": "counterspell",
    # Tokens
    "token": "token_maker",
    "tokens": "token_maker",
    "token_producer": "token_maker",
    "creates_tokens": "token_maker",
    "make_tokens": "token_maker",
    # Sacrifice
    "sac": "sacrifice_outlet",
    "sac_outlet": "sacrifice_outlet",
    "sacrifice": "sacrifice_outlet",
    "sacrifice_engine": "sacrifice_outlet",
    "free_sacrifice": "sacrifice_outlet",
    "free_sacrifice_outlet": "sacrifice_outlet",
    "sac_payoff": "sacrifice_payoff",
    "sacrifice_matters": "sacrifice_payoff",
    "aristocrats_payoff": "aristocrat_payoff",
    "death_trigger": "death_trigger_payoff",
    "death_triggers": "death_trigger_payoff",
    "dies_trigger": "death_trigger_payoff",
    "dies_triggers": "death_trigger_payoff",
    # Graveyard / recursion
    "self_mill": "graveyard_enabler",
    "mill_self": "graveyard_enabler",
    "looting": "discard_outlet",
    "loot": "discard_outlet",
    "reanimate": "reanimation",
    # Blink and interaction
    "flicker": "blink",
    "bounce": "blink",
    "spot_removal": "removal",
    "single_target_removal": "removal",
    "wrath": "board_wipe",
    "wipe": "board_wipe",
    "sweeper": "board_wipe",
    # Mana
    "mana_ramp": "ramp",
    "mana_rock": "ramp",
    "mana_dork": "ramp",
    "treasures": "treasure_maker",
    "treasure": "treasure_maker",
    "treasure_producer": "treasure_maker",
    # Other common wording
    "protection_spell": "protection",
    "cost_reduction": "cost_reducer",
    "combo": "combo_piece",
    "wincon": "finisher",
    "win_condition": "finisher",
}


FUNCTION_GROUPS: dict[str, frozenset[str]] = {
    "draw": frozenset({"draw", "impulse_draw"}),
    "ramp": frozenset({"ramp", "land_ramp", "treasure_maker"}),
    "removal": frozenset(
        {
            "removal",
            "creature_removal",
            "artifact_removal",
            "enchantment_removal",
        }
    ),
    "counterspell": frozenset({"counterspell"}),
    "board_wipe": frozenset({"board_wipe"}),
    "protection": frozenset({"protection"}),
    "tutor": frozenset({"tutor"}),
    "token_maker": frozenset({"token_maker"}),
    "sacrifice_outlet": frozenset({"sacrifice_outlet"}),
    "graveyard_enabler": frozenset({"graveyard_enabler", "discard_outlet"}),
    "recursion": frozenset({"recursion", "reanimation"}),
    "finisher": frozenset({"finisher", "combo_piece"}),
}


ROLE_BUCKETS: dict[str, str] = {
    "impulse_draw": "draw",
    "land_ramp": "ramp",
    "treasure_maker": "ramp",
    "creature_removal": "removal",
    "artifact_removal": "removal",
    "enchantment_removal": "removal",
    "reanimation": "recursion",
    "combo_piece": "finisher",
}


def _slug(value: str) -> str:
    return _NON_WORD.sub("_", value.strip().lower()).strip("_")


def normalize_function(value: str | None) -> str:
    """Return the canonical spelling for a function-like label."""
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    key = _slug(str(raw))
    return FUNCTION_ALIASES.get(key, key)


def normalize_functions(
    values: Iterable[str] | None, *, dedupe: bool = True
) -> list[str]:
    """Normalize and drop blanks, optionally de-duplicating in order."""
    if not values:
        return []
    if isinstance(values, str):
        values = [values]

    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_function(value)
        if not normalized:
            continue
        if dedupe and normalized in seen:
            continue
        out.append(normalized)
        seen.add(normalized)
    return out


def function_group(role: str) -> frozenset[str]:
    """Return all canonical functions that satisfy a high-level role."""
    normalized = normalize_function(role)
    return FUNCTION_GROUPS.get(normalized, frozenset({normalized}))


def role_bucket(value: str | None) -> str:
    """Collapse specialized functions into the role bucket used for counts."""
    normalized = normalize_function(value)
    return ROLE_BUCKETS.get(normalized, normalized)


def has_function(functions: Iterable[str], role: str) -> bool:
    """True if any normalized function satisfies the normalized role/group."""
    normalized = set(normalize_functions(functions))
    return bool(normalized.intersection(function_group(role)))
