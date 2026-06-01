"""
Dynamic role targets for Commander deck building.

Replaces fixed ramp=12, draw=10, etc. with commander-aware, archetype-aware,
bracket-aware targets. The commander profile transforms requirements — it doesn't
simply subtract counts, because a commander that draws still needs support pieces,
enablers, and redundancy. We adjust the *shape* of the need, not eliminate it.

target = base + commander_modifier + archetype_modifier + bracket_modifier + curve_modifier
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from mtg_evaluator.deckbuilding.commander_profile import CommanderProfileData

# Base targets by bracket — starting point before any modifiers.
# These are informed by community consensus for "typical" commanders.
_BASE_TARGETS: dict[int, dict[str, int]] = {
    1: {
        "lands": 38,
        "ramp": 10,
        "draw": 8,
        "removal": 5,
        "board_wipe": 2,
        "protection": 2,
        "finisher": 3,
    },
    2: {
        "lands": 37,
        "ramp": 11,
        "draw": 9,
        "removal": 6,
        "board_wipe": 2,
        "protection": 2,
        "finisher": 3,
    },
    3: {
        "lands": 36,
        "ramp": 12,
        "draw": 10,
        "removal": 7,
        "board_wipe": 2,
        "protection": 3,
        "finisher": 4,
    },
    4: {
        "lands": 32,
        "ramp": 14,
        "draw": 10,
        "removal": 6,
        "board_wipe": 2,
        "protection": 3,
        "finisher": 4,
    },
    5: {
        "lands": 29,
        "ramp": 16,
        "draw": 11,
        "removal": 5,
        "board_wipe": 1,
        "protection": 4,
        "finisher": 3,
    },
}

# Archetype modifiers — change the shape of needs per strategy.
# Positive = need more of this. Negative = need less. 0 = no change.
_ARCHETYPE_MODIFIERS: dict[str, dict[str, int]] = {
    "voltron": {
        "protection": +3,  # commander must survive
        "removal": -2,  # fewer generic removal, more evasion/protection
        "draw": -1,  # tutors for equipment/auras replace draw
        "finisher": -2,  # commander IS the finisher
    },
    "big_mana": {
        "ramp": +3,  # ramp is the whole plan
        "finisher": +2,  # big X spells / giant threats
        "removal": -1,
    },
    "aristocrats": {
        "removal": -2,  # sacrifice IS removal
        "finisher": -1,  # drain damage IS the win
    },
    "sacrifice": {
        "removal": -2,
        "finisher": -1,
    },
    "graveyard": {
        "draw": -3,  # graveyard = card advantage
        "ramp": -1,  # often self-mills into mana
    },
    "reanimator": {
        "draw": -2,  # tutors replace draw
        "ramp": -1,
        "finisher": -2,  # the reanimation targets ARE finishers
    },
    "tokens": {
        "removal": -1,  # token sacrifice covers some removal
        "finisher": -1,  # anthems + go-wide IS the plan
    },
    "spellslinger": {
        "draw": +2,  # need lots of spells/cantrips
        "ramp": +1,  # need mana for spells
    },
    "control": {
        "removal": +2,
        "board_wipe": +1,
        "draw": +1,
    },
    "stax": {
        "board_wipe": -1,  # symmetric locks replace wipes
        "removal": +1,
        "protection": +1,  # protect your stax pieces
    },
    "combo": {
        "draw": +2,  # need to dig for combo pieces
        "protection": +2,  # protect the combo
        "finisher": -3,  # combo IS the finisher
        "removal": -1,
    },
    "toolbox": {
        "draw": +1,  # need to find answers
    },
    "enchantress": {
        "draw": -2,  # enchantress cards = built-in draw engine
        "ramp": -1,
    },
    "artifacts": {
        "ramp": -1,  # artifact synergy often provides mana
    },
    "lands": {
        "ramp": +2,  # land ramp is central
        "draw": -1,  # lands in yard = card access
    },
    "blink": {
        "removal": -1,  # blinking provides tempo/pseudo-removal
        "draw": -1,  # ETB effects cover draw
    },
}

# Minimum values — no target can drop below these regardless of modifiers
_MINIMUMS: dict[str, int] = {
    "lands": 28,
    "ramp": 6,
    "draw": 4,
    "removal": 3,
    "board_wipe": 1,
    "protection": 1,
    "finisher": 1,
}

# Maximum values — no target should exceed these
_MAXIMUMS: dict[str, int] = {
    "lands": 42,
    "ramp": 20,
    "draw": 16,
    "removal": 12,
    "board_wipe": 5,
    "protection": 8,
    "finisher": 8,
}


@dataclass
class RoleTargets:
    """Dynamic role targets for a specific commander + archetype + bracket combination."""

    lands: int
    ramp: int
    draw: int
    removal: int
    board_wipe: int
    protection: int
    finisher: int
    extra: dict[str, int] = field(default_factory=dict)

    # Explanations for non-obvious adjustments (for UI display)
    modifiers_applied: list[str] = field(default_factory=list)

    def get(self, role: str, default: int = 0) -> int:
        return getattr(self, role, self.extra.get(role, default))

    def to_dict(self) -> dict[str, int]:
        return {
            "lands": self.lands,
            "ramp": self.ramp,
            "draw": self.draw,
            "removal": self.removal,
            "board_wipe": self.board_wipe,
            "protection": self.protection,
            "finisher": self.finisher,
            **self.extra,
        }


def compute_role_targets(
    bracket: int,
    profile: Optional[CommanderProfileData],
    archetype: Optional[str],
    avg_cmc: float = 3.5,
) -> RoleTargets:
    """
    Compute dynamic role targets given the full context.
    Commander profile transforms needs; archetype shapes the deck skeleton.
    """
    bracket = max(1, min(5, bracket))
    targets = dict(_BASE_TARGETS[bracket])
    mods: list[str] = []

    # --- Commander profile modifiers ---
    if profile:
        # Commander provides draw → need fewer draw spells, but don't drop below minimum
        if profile.provides_draw:
            delta = -3
            targets["draw"] = targets["draw"] + delta
            mods.append(f"draw {delta:+d} ({profile.card_name} provides draw)")

        if profile.provides_ramp:
            delta = -2
            targets["ramp"] = targets["ramp"] + delta
            mods.append(f"ramp {delta:+d} ({profile.card_name} provides ramp)")

        if profile.provides_protection:
            delta = -1
            targets["protection"] = targets["protection"] + delta
            mods.append(
                f"protection {delta:+d} ({profile.card_name} has built-in protection)"
            )

        if profile.provides_wincon:
            delta = -2
            targets["finisher"] = targets.get("finisher", 3) + delta
            mods.append(
                f"finisher {delta:+d} ({profile.card_name} is the win condition)"
            )

        if profile.provides_removal:
            delta = -1
            targets["removal"] = targets["removal"] + delta
            mods.append(f"removal {delta:+d} ({profile.card_name} provides removal)")

        # Commander needs more protection than normal
        if profile.protection_need >= 4:
            delta = +2
            targets["protection"] = targets["protection"] + delta
            mods.append(
                f"protection {delta:+d} (high protection need, score={profile.protection_need})"
            )
        elif profile.protection_need >= 3:
            delta = +1
            targets["protection"] = targets["protection"] + delta
            mods.append(f"protection {delta:+d} (elevated protection need)")

        # High commander dependency → need more protection and redundancy
        if profile.dependency_score >= 4:
            targets["protection"] = targets["protection"] + 1
            mods.append(f"protection +1 (deck highly dependent on commander)")

        # Commander needs specific card types → note it but don't change counts
        # (the pool builder weights these card types higher)

    # --- Archetype modifiers ---
    if archetype:
        arch_mods = _ARCHETYPE_MODIFIERS.get(archetype, {})
        for role, delta in arch_mods.items():
            targets[role] = targets.get(role, 0) + delta
            if delta != 0:
                mods.append(f"{role} {delta:+d} ({archetype} archetype)")

    # --- Curve modifier ---
    if avg_cmc > 4.5:
        targets["ramp"] = targets["ramp"] + 3
        mods.append(f"ramp +3 (high avg CMC: {avg_cmc:.1f})")
    elif avg_cmc > 3.8:
        targets["ramp"] = targets["ramp"] + 1
        mods.append(f"ramp +1 (above-average CMC: {avg_cmc:.1f})")
    elif avg_cmc < 2.5:
        targets["ramp"] = targets["ramp"] - 2
        targets["lands"] = targets["lands"] - 1
        mods.append(f"ramp -2, lands -1 (low avg CMC: {avg_cmc:.1f})")

    # --- Apply floor/ceiling ---
    for role in list(targets.keys()):
        mn = _MINIMUMS.get(role, 0)
        mx = _MAXIMUMS.get(role, 99)
        targets[role] = max(mn, min(mx, targets[role]))

    return RoleTargets(
        lands=targets["lands"],
        ramp=targets["ramp"],
        draw=targets["draw"],
        removal=targets["removal"],
        board_wipe=targets["board_wipe"],
        protection=targets.get("protection", 2),
        finisher=targets.get("finisher", 3),
        modifiers_applied=mods,
    )


def saturation_multiplier(role: str, current_count: int, target: int) -> float:
    """
    Diminishing returns modifier for role redundancy.
    Card value drops as we accumulate more pieces of the same role.
    Used to down-weight candidates when the pool already has enough of a role.
    """
    if target <= 0:
        return 0.3  # role not needed at all

    ratio = current_count / target
    if ratio < 0.5:
        return 1.3  # urgently need more
    elif ratio < 0.8:
        return 1.1  # still want more
    elif ratio < 1.0:
        return 1.0  # on target
    elif ratio < 1.3:
        return 0.8  # slightly over
    elif ratio < 1.6:
        return 0.6  # noticeably over
    else:
        return 0.4  # heavily over — unless archetype demands excess
