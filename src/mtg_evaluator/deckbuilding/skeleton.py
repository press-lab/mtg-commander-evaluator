"""
Deterministic deck skeleton builder — the "solver".

Given a scored CardPool and its dynamic role targets, greedily constructs a
complete 98/99-card deck that satisfies every role target with the
highest-scoring available cards. The LLM never builds the deck from scratch;
it only proposes bounded swaps against this baseline (see assembler.py).

Guarantees:
  - Exact deck size (basics fill any land shortfall — they need not be in the pool)
  - Role targets met or maximally approached given the pool
  - Double-duty cards credit every role they fill (no over-buying)
  - Color-deficit-driven basic land allocation (Karsten math via mana_analysis)
  - Complete combos included as units when the pool offers them

Because selection is by descending composite score within each role, the
skeleton is optimal-by-construction with respect to the scoring criteria
(bracket, archetype, style, budget, salt) — "no better options" as measured.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from mtg_evaluator.card_functions import normalize_functions, role_bucket
from mtg_evaluator.deckbuilding.mana_analysis import analyze_mana_base
from mtg_evaluator.deckbuilding.pool import CardPool, PoolCard

BASIC_BY_COLOR: dict[str, str] = {
    "W": "Plains",
    "U": "Island",
    "B": "Swamp",
    "R": "Mountain",
    "G": "Forest",
}
BASIC_LAND_NAMES: frozenset[str] = frozenset(BASIC_BY_COLOR.values()) | {
    "Wastes",
    "Snow-Covered Plains",
    "Snow-Covered Island",
    "Snow-Covered Swamp",
    "Snow-Covered Mountain",
    "Snow-Covered Forest",
}

# Role fill priority. Earlier roles get first pick of double-duty cards.
_ROLE_PRIORITY = ("ramp", "draw", "removal", "board_wipe", "protection", "finisher")

# Function buckets that satisfy each skeleton role
_ROLE_BUCKETS: dict[str, frozenset[str]] = {
    "ramp": frozenset({"ramp"}),
    "draw": frozenset({"draw"}),
    "removal": frozenset({"removal", "counterspell"}),
    "board_wipe": frozenset({"board_wipe"}),
    "protection": frozenset({"protection"}),
    "finisher": frozenset({"finisher"}),
}


@dataclass
class SkeletonDeck:
    lands: list[str] = field(default_factory=list)
    ramp: list[str] = field(default_factory=list)
    draw: list[str] = field(default_factory=list)
    removal: list[str] = field(default_factory=list)
    synergy: list[str] = field(default_factory=list)
    combo: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)

    rationale: dict[str, str] = field(default_factory=dict)  # card -> why
    role_fill: dict[str, int] = field(default_factory=dict)  # role -> achieved

    @property
    def all_cards(self) -> list[str]:
        return (
            self.lands + self.ramp + self.draw + self.removal
            + self.synergy + self.combo + self.other
        )

    @property
    def total(self) -> int:
        return len(self.all_cards)


def _card_roles(card: PoolCard) -> set[str]:
    """Skeleton roles this card credits (via normalized function buckets)."""
    buckets = {role_bucket(fn) for fn in normalize_functions(card.functions)}
    return {
        role for role, accepted in _ROLE_BUCKETS.items()
        if buckets & accepted
    }


def _allocate_basics(
    nonbasic_names: list[str],
    slots: int,
    pool: CardPool,
) -> list[str]:
    """
    Fill remaining land slots with basics, driven by color deficits from the
    Karsten analysis of the chosen nonbasics, then by commander pip weights.
    """
    if slots <= 0:
        return []

    identity_colors = [c for c in pool.color_identity if c in BASIC_BY_COLOR]
    if not identity_colors:
        return ["Wastes"] * slots

    analysis = analyze_mana_base(
        land_names=nonbasic_names,
        commander_mana_cost=pool.commander_mana_cost,
        commander_cmc=pool.commander_cmc,
        deck_color_identity=pool.color_identity,
        partner_mana_cost=pool.partner_mana_cost,
    )

    basics: list[str] = []
    deficits = Counter(
        {c: n for c, n in analysis.color_deficits.items() if c in BASIC_BY_COLOR}
    )
    # 1. Cover hard deficits first
    while slots > 0 and any(v > 0 for v in deficits.values()):
        color = deficits.most_common(1)[0][0]
        basics.append(BASIC_BY_COLOR[color])
        deficits[color] -= 1
        slots -= 1

    # 2. Distribute the rest by pip weight (even split when pips are equal)
    pip_weights = {
        c: max(1, analysis.pip_requirements.get(c, 0)) for c in identity_colors
    }
    counts = Counter({c: 0 for c in identity_colors})
    while slots > 0:
        # Pick the color whose share is furthest below its pip weight
        color = min(identity_colors, key=lambda c: counts[c] / pip_weights[c])
        basics.append(BASIC_BY_COLOR[color])
        counts[color] += 1
        slots -= 1
    return basics


def build_skeleton(pool: CardPool) -> SkeletonDeck:
    """Deterministically solve the deck against the pool's role targets."""
    deck = SkeletonDeck()
    has_partner = " + " in pool.commander_name
    deck_size = 98 if has_partner else 99
    targets = pool.role_targets.to_dict() if pool.role_targets else {}
    land_target = targets.get("lands", 36)

    picked: set[str] = set()
    role_credit: dict[str, int] = {role: 0 for role in _ROLE_PRIORITY}

    cards_by_name = {c.name: c for c in pool.all_cards}
    nonland_sorted = sorted(
        (c for c in pool.all_cards if "land" not in c.type_line.lower()),
        key=lambda c: c.score,
        reverse=True,
    )
    land_sorted = sorted(
        (c for c in pool.all_cards if "land" in c.type_line.lower()),
        key=lambda c: c.score,
        reverse=True,
    )

    # --- 1. Lands: best nonbasics, then deficit-driven basics ---
    # Keep at least a few basic slots for color stability and budget sanity.
    min_basics = 3 if len(pool.color_identity) >= 3 else 6
    nonbasic_cap = max(0, land_target - min_basics)
    for card in land_sorted:
        if len(deck.lands) >= nonbasic_cap:
            break
        if card.name in BASIC_LAND_NAMES or card.name in picked:
            continue
        deck.lands.append(card.name)
        picked.add(card.name)
        deck.rationale[card.name] = f"land (tier score {card.score:.0f})"

    basics = _allocate_basics(list(deck.lands), land_target - len(deck.lands), pool)
    for name in basics:
        deck.lands.append(name)
    if basics:
        counts = Counter(basics)
        for name, n in counts.items():
            deck.rationale[name] = f"basic ×{n} (color requirements)"

    # --- 2. Complete combos as units (first legal combo whose cards all exist) ---
    for combo in pool.combos[:3]:
        names = [n for n in combo.card_names if n in cards_by_name]
        if len(names) != len(combo.card_names):
            continue  # combo not fully available in pool
        if any(n in picked for n in names):
            continue
        for n in names:
            deck.combo.append(n)
            picked.add(n)
            deck.rationale[n] = f"combo piece ({' + '.join(combo.card_names)})"
            for role in _card_roles(cards_by_name[n]):
                role_credit[role] += 1
        break  # one combo package is enough for the skeleton

    # --- 3. Fill roles in priority order, crediting double-duty ---
    nonland_budget = deck_size - len(deck.lands)
    for role in _ROLE_PRIORITY:
        target = targets.get(role, 0)
        for card in nonland_sorted:
            if role_credit[role] >= target:
                break
            if deck.total - len(deck.lands) >= nonland_budget:
                break
            if card.name in picked:
                continue
            roles = _card_roles(card)
            if role not in roles:
                continue
            category = role if role in ("ramp", "draw", "removal") else "synergy"
            getattr(deck, category).append(card.name)
            picked.add(card.name)
            for r in roles:
                role_credit[r] += 1
            extra = (
                f", also {', '.join(sorted(roles - {role}))}" if len(roles) > 1 else ""
            )
            deck.rationale[card.name] = (
                f"{role.replace('_', ' ')} {role_credit[role]}/{target} "
                f"(score {card.score:.0f}{extra})"
            )

    # --- 4. Flex: best remaining cards by score ---
    while deck.total < deck_size:
        filled = False
        for card in nonland_sorted:
            if card.name in picked:
                continue
            category = "synergy" if (card.archetype_score or 0) >= 3 else "other"
            getattr(deck, category).append(card.name)
            picked.add(card.name)
            for r in _card_roles(card):
                role_credit[r] += 1
            deck.rationale[card.name] = f"best available (score {card.score:.0f})"
            filled = True
            break
        if not filled:
            # Pool exhausted — pad with basics so the count is always exact
            color = pool.color_identity[0] if pool.color_identity else None
            deck.lands.append(BASIC_BY_COLOR.get(color or "", "Wastes"))
            deck.rationale.setdefault("(padding basics)", "pool exhausted")

    deck.role_fill = dict(role_credit)
    deck.role_fill["lands"] = len(deck.lands)
    return deck
