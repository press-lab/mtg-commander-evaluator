"""
Archetype package health checks and nonbo detection.

Packages model the enabler/payoff/support structure required for an archetype
to function. An aristocrats deck without sac outlets, or a reanimator deck
without targets, will fail regardless of card quality.

Nonbos are card-level conflicts within the deck context — graveyard hate in a
graveyard deck, symmetric wipes in a go-wide deck, etc. Severity and confidence
are explicit so the UI can surface warnings without hard-blocking recommendations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Archetype package definitions
# ---------------------------------------------------------------------------

@dataclass
class PackageDefinition:
    archetype: str
    description: str
    enabler_functions: list[str]     # card functions that count as enablers
    payoff_functions: list[str]      # card functions that count as payoffs
    support_functions: list[str]     # nice-to-have support
    min_enablers: int                # minimum enablers for package to function
    min_payoffs: int                 # minimum payoffs for package to function
    ideal_enabler_payoff_ratio: float  # enablers:payoffs ideal ratio


ARCHETYPE_PACKAGES: dict[str, PackageDefinition] = {
    "tokens": PackageDefinition(
        archetype="tokens",
        description="Token creation engine + anthem payoffs",
        enabler_functions=["token_maker", "tokens", "token_producer"],
        payoff_functions=["anthem", "token_payoff", "go_wide_payoff", "overrun"],
        support_functions=["protection", "sacrifice"],
        min_enablers=8, min_payoffs=5,
        ideal_enabler_payoff_ratio=1.5,
    ),
    "aristocrats": PackageDefinition(
        archetype="aristocrats",
        description="Sacrifice engine: outlets + death triggers + fodder",
        enabler_functions=["sac_outlet", "sacrifice_outlet"],
        payoff_functions=["death_trigger", "sacrifice_payoff", "aristocrat_payoff"],
        support_functions=["token_maker", "recursion"],
        min_enablers=4, min_payoffs=6,
        ideal_enabler_payoff_ratio=0.7,
    ),
    "sacrifice": PackageDefinition(
        archetype="sacrifice",
        description="Free sacrifice engine with recurring fodder",
        enabler_functions=["sac_outlet", "sacrifice_outlet", "free_sacrifice"],
        payoff_functions=["death_trigger", "sacrifice_payoff"],
        support_functions=["token_maker", "recursion"],
        min_enablers=4, min_payoffs=5,
        ideal_enabler_payoff_ratio=0.8,
    ),
    "reanimator": PackageDefinition(
        archetype="reanimator",
        description="Discard/mill enablers + reanimation spells + high-MV targets",
        enabler_functions=["discard_outlet", "self_mill", "entomb"],
        payoff_functions=["reanimation", "reanimate"],
        support_functions=["protection", "recursion"],
        min_enablers=4, min_payoffs=5,
        ideal_enabler_payoff_ratio=0.8,
    ),
    "graveyard": PackageDefinition(
        archetype="graveyard",
        description="Self-mill + graveyard recursion/value",
        enabler_functions=["self_mill", "discard_outlet", "looting"],
        payoff_functions=["graveyard_payoff", "recursion", "flashback"],
        support_functions=["protection"],
        min_enablers=6, min_payoffs=6,
        ideal_enabler_payoff_ratio=1.0,
    ),
    "blink": PackageDefinition(
        archetype="blink",
        description="ETB flicker engine + ETB payoffs",
        enabler_functions=["blink", "flicker", "bounce"],
        payoff_functions=["etb_payoff", "etb_trigger"],
        support_functions=["protection"],
        min_enablers=5, min_payoffs=8,
        ideal_enabler_payoff_ratio=0.6,
    ),
    "voltron": PackageDefinition(
        archetype="voltron",
        description="Equipment/aura enablers + combat payoffs + protection",
        enabler_functions=["equipment", "aura", "equipment_tutor"],
        payoff_functions=["combat_damage_trigger", "attack_trigger"],
        support_functions=["protection", "evasion", "haste"],
        min_enablers=8, min_payoffs=2,
        ideal_enabler_payoff_ratio=4.0,
    ),
    "spellslinger": PackageDefinition(
        archetype="spellslinger",
        description="Spell-casting triggers + cantrips + payoffs",
        enabler_functions=["cantrip", "spells"],
        payoff_functions=["spell_payoff", "magecraft", "storm_payoff"],
        support_functions=["ramp", "draw"],
        min_enablers=12, min_payoffs=6,
        ideal_enabler_payoff_ratio=2.0,
    ),
    "artifacts": PackageDefinition(
        archetype="artifacts",
        description="Artifact synergy: producers + payoffs",
        enabler_functions=["artifact", "artifact_producer"],
        payoff_functions=["artifact_payoff", "affinity"],
        support_functions=["recursion"],
        min_enablers=12, min_payoffs=5,
        ideal_enabler_payoff_ratio=2.5,
    ),
    "enchantress": PackageDefinition(
        archetype="enchantress",
        description="Enchantments + enchantress draw engines",
        enabler_functions=["enchantment", "aura"],
        payoff_functions=["enchantress_payoff", "constellation"],
        support_functions=["protection"],
        min_enablers=14, min_payoffs=4,
        ideal_enabler_payoff_ratio=3.5,
    ),
    "lands": PackageDefinition(
        archetype="lands",
        description="Land ramp + landfall payoffs",
        enabler_functions=["land_ramp", "extra_land_drop"],
        payoff_functions=["landfall", "land_payoff"],
        support_functions=["recursion"],
        min_enablers=8, min_payoffs=6,
        ideal_enabler_payoff_ratio=1.3,
    ),
    "big_mana": PackageDefinition(
        archetype="big_mana",
        description="Ramp + high-MV payoffs / X spells",
        enabler_functions=["ramp", "land_ramp", "mana_doubler"],
        payoff_functions=["high_mv_payoff", "x_spell", "finisher"],
        support_functions=["draw"],
        min_enablers=12, min_payoffs=4,
        ideal_enabler_payoff_ratio=3.0,
    ),
    "combo": PackageDefinition(
        archetype="combo",
        description="Combo pieces + tutors to assemble + protection",
        enabler_functions=["tutor", "cantrip"],
        payoff_functions=["combo_piece", "combo_payoff"],
        support_functions=["protection", "counterspell"],
        min_enablers=6, min_payoffs=4,
        ideal_enabler_payoff_ratio=1.5,
    ),
}


@dataclass
class PackageHealth:
    """Health report for an archetype's package structure."""
    archetype: str
    description: str
    enabler_count: int
    payoff_count: int
    support_count: int
    min_enablers: int
    min_payoffs: int
    enabler_deficit: int
    payoff_deficit: int
    ratio: float
    ideal_ratio: float
    is_healthy: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "archetype": self.archetype,
            "description": self.description,
            "enabler_count": self.enabler_count,
            "payoff_count": self.payoff_count,
            "support_count": self.support_count,
            "min_enablers": self.min_enablers,
            "min_payoffs": self.min_payoffs,
            "enabler_deficit": self.enabler_deficit,
            "payoff_deficit": self.payoff_deficit,
            "ratio": round(self.ratio, 2),
            "is_healthy": self.is_healthy,
            "warnings": self.warnings,
        }


def check_package_health(
    archetype: Optional[str],
    all_functions: list[str],  # flat list of all functions across all cards in pool
) -> Optional[PackageHealth]:
    """
    Check the enabler/payoff balance for the given archetype.
    Returns None if archetype has no package definition.
    """
    if not archetype or archetype not in ARCHETYPE_PACKAGES:
        return None

    pkg = ARCHETYPE_PACKAGES[archetype]
    fn_set = all_functions  # flat list, may have duplicates (one per card role)

    enablers = sum(1 for f in fn_set if f in pkg.enabler_functions)
    payoffs  = sum(1 for f in fn_set if f in pkg.payoff_functions)
    support  = sum(1 for f in fn_set if f in pkg.support_functions)

    enabler_deficit = max(0, pkg.min_enablers - enablers)
    payoff_deficit  = max(0, pkg.min_payoffs  - payoffs)
    ratio = enablers / payoffs if payoffs > 0 else float(enablers)
    is_healthy = enabler_deficit == 0 and payoff_deficit == 0

    warns = []
    if enabler_deficit > 0:
        warns.append(
            f"{archetype}: only {enablers} enablers (need {pkg.min_enablers}). "
            f"Add {enabler_deficit} more {pkg.enabler_functions[0]} effects."
        )
    if payoff_deficit > 0:
        warns.append(
            f"{archetype}: only {payoffs} payoffs (need {pkg.min_payoffs}). "
            f"Add {payoff_deficit} more {pkg.payoff_functions[0]} effects."
        )
    if ratio > pkg.ideal_enabler_payoff_ratio * 2 and payoffs > 0:
        warns.append(
            f"{archetype}: enabler/payoff ratio is {ratio:.1f} (ideal ~{pkg.ideal_enabler_payoff_ratio:.1f}). "
            "Too many enablers, not enough payoffs."
        )
    if ratio < pkg.ideal_enabler_payoff_ratio * 0.4 and enablers > 0:
        warns.append(
            f"{archetype}: enabler/payoff ratio is {ratio:.1f} (ideal ~{pkg.ideal_enabler_payoff_ratio:.1f}). "
            "Too many payoffs, not enough enablers."
        )

    return PackageHealth(
        archetype=archetype,
        description=pkg.description,
        enabler_count=enablers,
        payoff_count=payoffs,
        support_count=support,
        min_enablers=pkg.min_enablers,
        min_payoffs=pkg.min_payoffs,
        enabler_deficit=enabler_deficit,
        payoff_deficit=payoff_deficit,
        ratio=ratio,
        ideal_ratio=pkg.ideal_enabler_payoff_ratio,
        is_healthy=is_healthy,
        warnings=warns,
    )


# ---------------------------------------------------------------------------
# Nonbo detection
# ---------------------------------------------------------------------------

@dataclass
class NonboWarning:
    rule_id: str
    severity: str        # "high" | "medium" | "low"
    confidence: float    # 0-1
    message: str
    card_name: Optional[str] = None  # specific card if applicable

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "confidence": self.confidence,
            "message": self.message,
            "card_name": self.card_name,
        }


def detect_nonbos(
    archetype: Optional[str],
    all_functions: list[str],         # flat list of functions
    all_card_names: list[str],        # all card names in pool
    all_card_functions: dict[str, list[str]],  # name → functions
    ramp_count: int,
    avg_cmc: float,
    land_count: int,
    board_wipe_count: int,
    creature_count: int,
) -> list[NonboWarning]:
    """Rule-based nonbo detection. Returns warnings, not hard bans."""
    warnings = []
    fn_set = set(all_functions)

    # 1. Graveyard hate in a graveyard deck
    if archetype in ("graveyard", "reanimator"):
        gy_hate_fns = {"exile_graveyard", "graveyard_hate", "relic_effect"}
        for card, fns in all_card_functions.items():
            if any(f in gy_hate_fns for f in fns):
                warnings.append(NonboWarning(
                    rule_id="gy_deck_gy_hate",
                    severity="high", confidence=0.9,
                    message=f"{card} exiles graveyards — directly conflicts with {archetype} gameplan.",
                    card_name=card,
                ))

    # 2. Symmetric board wipes in a go-wide creature strategy
    if archetype in ("tokens", "combat", "aristocrats"):
        if board_wipe_count >= 3:
            warnings.append(NonboWarning(
                rule_id="go_wide_many_wipes",
                severity="medium", confidence=0.75,
                message=f"{board_wipe_count} board wipes in a {archetype} deck risks destroying your own board. Keep ≤2 asymmetric wipes.",
            ))

    # 3. Expensive top-end without ramp
    if avg_cmc > 4.0 and ramp_count < 8:
        warnings.append(NonboWarning(
            rule_id="high_cmc_low_ramp",
            severity="high", confidence=0.85,
            message=f"Average CMC {avg_cmc:.1f} with only {ramp_count} ramp pieces. Will consistently miss curve.",
        ))

    # 4. Tapland overload in fast-start archetype
    if archetype in ("combo", "stax") and land_count > 0:
        tapland_fns = [f for f in all_functions if f == "tapland"]
        tapland_approx = sum(1 for n in all_card_names if any(
            x in n for x in ["Gate", "Guildgate", "Refuge", "Hollow", "Cave", "Spring", "Cove"]
        ))
        if tapland_approx > 6:
            warnings.append(NonboWarning(
                rule_id="fast_deck_taplands",
                severity="medium", confidence=0.7,
                message=f"~{tapland_approx} taplands in a {archetype} deck hurt speed significantly.",
            ))

    # 5. Payoffs without enablers (archetype-specific)
    if archetype in ("reanimator", "graveyard"):
        has_enablers = any(f in fn_set for f in ("self_mill", "discard_outlet", "entomb", "looting"))
        has_payoffs  = any(f in fn_set for f in ("reanimation", "graveyard_payoff"))
        if has_payoffs and not has_enablers:
            warnings.append(NonboWarning(
                rule_id="reanimator_no_enablers",
                severity="high", confidence=0.88,
                message="Reanimation payoffs present but no discard/mill enablers to get targets into graveyard.",
            ))

    if archetype in ("tokens", "aristocrats"):
        has_payoffs  = any(f in fn_set for f in ("anthem", "death_trigger", "sacrifice_payoff", "aristocrat_payoff"))
        has_enablers = any(f in fn_set for f in ("token_maker", "sac_outlet", "sacrifice_outlet"))
        if has_payoffs and not has_enablers:
            warnings.append(NonboWarning(
                rule_id="no_enablers_for_payoffs",
                severity="high", confidence=0.85,
                message=f"{archetype}: payoffs present but no enablers (token makers / sac outlets).",
            ))

    # 6. Enablers without finishers
    if archetype in ("combo", "spellslinger"):
        has_enablers = any(f in fn_set for f in ("tutor", "cantrip", "combo_piece"))
        has_finisher = any(f in fn_set for f in ("combo_payoff", "finisher", "wincon", "storm_payoff"))
        if has_enablers and not has_finisher:
            warnings.append(NonboWarning(
                rule_id="no_finisher",
                severity="medium", confidence=0.75,
                message=f"{archetype}: has enablers/tutors but no clear win condition.",
            ))

    # 7. Creature-light in a combat/tribal/aristocrats deck
    if archetype in ("combat", "tribal", "tokens") and creature_count < 20:
        warnings.append(NonboWarning(
            rule_id="low_creature_count_combat",
            severity="medium", confidence=0.8,
            message=f"Only ~{creature_count} creatures in a {archetype} deck. Most strategies need 25-35+.",
        ))

    # 8. Stax pieces in a casual bracket (B1/B2) — noted but not blocked
    # (handled by bracket filter in pool builder — not repeated here)

    return warnings
