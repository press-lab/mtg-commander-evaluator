from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mtg_evaluator.card_functions import (
    has_function,
    normalize_function,
    normalize_functions,
    role_bucket,
)
from mtg_evaluator.db.models import (
    Card,
    CardClassification,
    CardFunction,
    CardArchetypeScore,
    CardPowerScore,
    CardBracketScore,
    EDHRecCardStats,
    Decklist,
    DeckCard,
    DeckEvaluation,
)
from mtg_evaluator.evaluation.parser import ParsedDecklist, ParsedCard
from mtg_evaluator.evaluation.bracket_llm import classify_bracket, BracketResult
from mtg_evaluator.db.models import SpellbookCombo, SpellbookComboCard
from mtg_evaluator.deckbuilding.commander_profile import (
    CommanderProfileData,
    get_or_generate_profile,
)
from mtg_evaluator.deckbuilding.consistency import (
    ConsistencyReport,
    compute_consistency,
)
from mtg_evaluator.deckbuilding.mana_analysis import ManaAnalysis, analyze_mana_base
from mtg_evaluator.deckbuilding.packages import (
    PackageHealth,
    NonboWarning,
    check_package_health,
    detect_nonbos,
    ARCHETYPE_PACKAGES,
)
from mtg_evaluator.deckbuilding.role_quality import compute_role_quality
from mtg_evaluator.deckbuilding.role_targets import RoleTargets, compute_role_targets

# Minimum recommended counts per function for a well-structured deck.
# "removal" here counts *all* targeted interaction (removal + creature_removal).
# Board wipes and protection are tracked separately.
_ROLE_MINIMUMS = {
    "ramp": 10,
    "draw": 8,
    "removal": 6,  # targeted removal (any type): target 6-8
    "board_wipe": 2,
    "protection": 2,
}

_BRACKET_LABELS = ["casual", "bracket_2", "bracket_3", "bracket_4", "cedh"]
_BRACKET_INT = {
    "casual": 1,
    "bracket_1": 1,
    "bracket_2": 2,
    "bracket_3": 3,
    "bracket_4": 4,
    "cedh": 5,
}

# Hard B4 floor — mass land denial cards (Brackets 1-3 explicitly prohibit these)
MASS_LAND_DENIAL: frozenset[str] = frozenset(
    {
        "Armageddon",
        "Ravages of War",
        "Catastrophe",
        "Decree of Annihilation",
        "Ruination",
        "From the Ashes",
        "Jokulhaups",
        "Obliterate",
        "Myojin of Infinite Rage",
        "Impending Disaster",
        "Boom // Bust",
        "Winter Orb",
        "Static Orb",
        "Stasis",
        "Mana Vortex",
        "Land Equilibrium",
        "Tectonic Break",
        "Sunder",
        "Keldon Firebombers",
        "Thoughts of Ruin",
        "Cataclysm",
        "Apocalypse",
    }
)

# Extra turn cards — standalone extra turns are B3-ok; only infinite loops (via Spellbook
# R-tagged combos) constitute a hard B4 floor. We track presence for LLM context.
EXTRA_TURN_CARDS: frozenset[str] = frozenset(
    {
        "Time Walk",
        "Temporal Manipulation",
        "Time Warp",
        "Temporal Mastery",
        "Nexus of Fate",
        "Alrund's Epiphany",
        "Expropriate",
        "Beacon of Tomorrows",
        "Part the Waterveil",
        "Capture of Jingzhou",
        "Walk the Aeons",
        "Savor the Moment",
        "Emrakul, the Promised End",
        "Magistrate's Scepter",
        "Medomai the Ageless",
        "Seedtime",
        "Notorious Throng",
        "Karn's Temporal Sundering",
        "Temporal Trespass",
        "Teferi, Master of Time",
        "Wandering Archaic",
    }
)

# Official Wizards of the Coast Game Changers list (February 2026, 53 cards).
# Brackets 1–2: 0 allowed. Bracket 3: up to 3. Brackets 4–5: unlimited.
# Source: https://magic.wizards.com/en/news/announcements/commander-brackets-beta-update-february-9-2026
GAME_CHANGERS: frozenset[str] = frozenset(
    {
        # White
        "Drannith Magistrate",
        "Enlightened Tutor",
        "Farewell",
        "Humility",
        "Teferi's Protection",
        "Smothering Tithe",
        # Blue
        "Consecrated Sphinx",
        "Cyclonic Rift",
        "Force of Will",
        "Fierce Guardianship",
        "Gifts Ungiven",
        "Intuition",
        "Mystical Tutor",
        "Narset, Parter of Veils",
        "Rhystic Study",
        "Thassa's Oracle",
        # Black
        "Ad Nauseam",
        "Bolas's Citadel",
        "Braids, Cabal Minion",
        "Demonic Tutor",
        "Imperial Seal",
        "Necropotence",
        "Opposition Agent",
        "Orcish Bowmasters",
        "Tergrid, God of Fright // Tergrid's Lantern",
        "Vampiric Tutor",
        # Red
        "Gamble",
        "Jeska's Will",
        "Underworld Breach",
        # Green
        "Biorhythm",
        "Crop Rotation",
        "Natural Order",
        "Seedborn Muse",
        "Survival of the Fittest",
        "Worldly Tutor",
        # Multicolor
        "Aura Shards",
        "Coalition Victory",
        "Grand Arbiter Augustin IV",
        "Notion Thief",
        # Colorless / Lands
        "Ancient Tomb",
        "Chrome Mox",
        "Field of the Dead",
        "Gaea's Cradle",
        "Glacial Chasm",
        "Grim Monolith",
        "Lion's Eye Diamond",
        "Mana Vault",
        "Mishra's Workshop",
        "Mox Diamond",
        "Panoptic Mirror",
        "Serra's Sanctum",
        "The One Ring",
        "The Tabernacle at Pendrell Vale",
    }
)


@dataclass
class ComboHit:
    spellbook_id: str
    bracket_tag: str  # R/S/P/O/C/E
    card_names: list[str]
    results_description: str | None


def _find_combos(session: Session, oracle_ids: set[str]) -> list[ComboHit]:
    """Return all Spellbook combos where every card's oracle_id is in the deck."""
    if not oracle_ids:
        return []

    # Find combo_ids where ALL cards have oracle_ids in the deck
    # We only match combos where every card was resolved to an oracle_id
    rows = session.execute(
        select(SpellbookComboCard.combo_id, SpellbookComboCard.oracle_id).where(
            SpellbookComboCard.oracle_id.isnot(None)
        )
    ).all()

    # Group by combo_id
    combo_oracles: dict[str, set[str]] = {}
    for combo_id, oracle_id in rows:
        combo_oracles.setdefault(combo_id, set()).add(oracle_id)

    matched_ids = [
        cid
        for cid, card_set in combo_oracles.items()
        if card_set and card_set.issubset(oracle_ids)
    ]
    if not matched_ids:
        return []

    combos = (
        session.execute(
            select(SpellbookCombo).where(SpellbookCombo.spellbook_id.in_(matched_ids))
        )
        .scalars()
        .all()
    )

    hits = []
    for combo in combos:
        card_names = [c.card_name for c in combo.cards if c.oracle_id in oracle_ids]
        hits.append(
            ComboHit(
                spellbook_id=combo.spellbook_id,
                bracket_tag=combo.bracket_tag or "E",
                card_names=card_names,
                results_description=combo.results_description,
            )
        )
    return hits


def _count_game_changers(card_names: list[str]) -> list[str]:
    """Return the game changer card names present in the deck."""
    found = []
    for name in card_names:
        # Handle DFC / split names — check both full name and front face
        if name in GAME_CHANGERS:
            found.append(name)
        elif " // " in name and name.split(" // ")[0] in GAME_CHANGERS:
            found.append(name)
    return found


@dataclass
class RoleCoverage:
    function: str
    count: int
    minimum: int
    average_quality: float | None = None

    @property
    def gap(self) -> int:
        return max(0, self.minimum - self.count)

    @property
    def meets_minimum(self) -> bool:
        return self.count >= self.minimum


@dataclass
class EvaluationResult:
    decklist_id: str
    commander_name: str | None
    archetype_guess: str | None
    bracket_estimate: str | None
    bracket_reasoning: str | None
    game_changers_found: list[str]
    combos_found: list[ComboHit]
    mass_land_denial_found: list[str]
    extra_turns_found: list[str]
    synergy_score: float | None
    role_coverage: list[RoleCoverage]
    gaps: list[str]
    unclassified_cards: list[str]
    unresolved_cards: list[str]
    improvement_suggestions: list[dict]
    commander_profile: CommanderProfileData | None = None
    role_targets: RoleTargets | None = None
    mana_analysis: ManaAnalysis | None = None
    consistency: ConsistencyReport | None = None
    package_health: PackageHealth | None = None
    nonbo_warnings: list[NonboWarning] = field(default_factory=list)


def _get_classifications(session: Session, oracle_ids: list[str]) -> dict[str, dict]:
    """Returns {oracle_id: {functions, archetype_scores, power_scores, bracket_scores}}"""
    result: dict[str, dict] = {
        oid: {
            "functions": [],
            "archetype_scores": {},
            "power_scores": {},
            "bracket_scores": {},
        }
        for oid in oracle_ids
    }

    # Latest valid classification per card
    valid_cls = session.execute(
        select(CardClassification.id, CardClassification.oracle_id)
        .where(CardClassification.oracle_id.in_(oracle_ids))
        .where(CardClassification.is_valid == True)  # noqa: E712
        .order_by(CardClassification.oracle_id, CardClassification.classified_at.desc())
        .distinct(CardClassification.oracle_id)
    ).all()

    cls_ids = [row.id for row in valid_cls]
    cls_by_oracle = {row.oracle_id: row.id for row in valid_cls}

    if not cls_ids:
        return result

    for row in session.execute(
        select(CardFunction.classification_id, CardFunction.function_name).where(
            CardFunction.classification_id.in_(cls_ids)
        )
    ).all():
        oracle_id = next(
            (o for o, c in cls_by_oracle.items() if c == row.classification_id), None
        )
        if oracle_id:
            fn = normalize_function(row.function_name)
            if fn and fn not in result[oracle_id]["functions"]:
                result[oracle_id]["functions"].append(fn)

    for row in session.execute(
        select(
            CardArchetypeScore.classification_id,
            CardArchetypeScore.archetype,
            CardArchetypeScore.score,
        ).where(CardArchetypeScore.classification_id.in_(cls_ids))
    ).all():
        oracle_id = next(
            (o for o, c in cls_by_oracle.items() if c == row.classification_id), None
        )
        if oracle_id:
            result[oracle_id]["archetype_scores"][row.archetype] = row.score

    for row in session.execute(
        select(
            CardPowerScore.classification_id, CardPowerScore.role, CardPowerScore.score
        ).where(CardPowerScore.classification_id.in_(cls_ids))
    ).all():
        oracle_id = next(
            (o for o, c in cls_by_oracle.items() if c == row.classification_id), None
        )
        if oracle_id:
            result[oracle_id]["power_scores"][row.role] = row.score

    for row in session.execute(
        select(
            CardBracketScore.classification_id,
            CardBracketScore.bracket_level,
            CardBracketScore.score,
        ).where(CardBracketScore.classification_id.in_(cls_ids))
    ).all():
        oracle_id = next(
            (o for o, c in cls_by_oracle.items() if c == row.classification_id), None
        )
        if oracle_id:
            result[oracle_id]["bracket_scores"][row.bracket_level] = row.score

    return result


def _guess_archetype(classifications: dict[str, dict]) -> str | None:
    totals: dict[str, float] = {}
    for cls in classifications.values():
        for archetype, score in cls["archetype_scores"].items():
            totals[archetype] = totals.get(archetype, 0) + score
    if not totals:
        return None
    return max(totals, key=lambda a: totals[a])


def _estimate_bracket(
    game_changer_names: list[str],
    classifications: dict[str, dict],
) -> tuple[str, int]:
    """
    Official WotC bracket rules (deterministic, top-down):
      0 game changers   → bracket_2
      1–3 game changers → bracket_3
      4+ game changers  → bracket_4 or cedh

    For bracket_4 vs cEDH: use the LLM's average cedh score as a tiebreaker.
    A deck starts at the highest bracket and only drops when it proves it lacks the
    cards to stay there.

    Returns (bracket_label, game_changer_count).
    """
    gc_count = len(game_changer_names)

    if gc_count >= 4:
        cedh_scores = [
            cls["bracket_scores"].get("cedh", 0)
            for cls in classifications.values()
            if cls["bracket_scores"]
        ]
        cedh_avg = sum(cedh_scores) / len(cedh_scores) if cedh_scores else 0
        bracket = "cedh" if cedh_avg >= 3.5 else "bracket_4"
    elif gc_count >= 1:
        bracket = "bracket_3"
    else:
        bracket = "bracket_2"

    return bracket, gc_count


def _compute_synergy_score(
    classifications: dict[str, dict], archetype: str | None
) -> float | None:
    if not archetype or not classifications:
        return None
    scores = [
        cls["archetype_scores"][archetype]
        for cls in classifications.values()
        if archetype in cls["archetype_scores"]
    ]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 2)


def _role_coverage(
    parsed_cards: list[ParsedCard],
    classifications: dict[str, dict],
    cards_by_oracle: dict[str, Card],
    targets: RoleTargets | None = None,
) -> list[RoleCoverage]:
    function_counts: dict[str, int] = {}
    quality_totals: dict[str, float] = {}
    quality_counts: dict[str, int] = {}

    for parsed_card in parsed_cards:
        cls = classifications.get(parsed_card.oracle_id, {})
        functions = normalize_functions(cls.get("functions", []))
        card_roles = {role_bucket(fn) for fn in functions}
        card = cards_by_oracle.get(parsed_card.oracle_id)

        for role in card_roles:
            function_counts[role] = function_counts.get(role, 0) + parsed_card.quantity

            if card and role not in {"lands"}:
                quality = compute_role_quality(
                    role=role,
                    cmc=float(card.cmc or 0),
                    is_instant=bool(card.is_instant),
                    is_sorcery=bool(card.is_sorcery),
                    functions=functions,
                    is_game_changer=card.name in GAME_CHANGERS,
                    oracle_text=card.oracle_text or "",
                )
                quality_totals[role] = quality_totals.get(role, 0.0) + (
                    quality * parsed_card.quantity
                )
                quality_counts[role] = (
                    quality_counts.get(role, 0) + parsed_card.quantity
                )

        if card and card.is_land:
            function_counts["lands"] = (
                function_counts.get("lands", 0) + parsed_card.quantity
            )

    coverage = []
    target_map = targets.to_dict() if targets else dict(_ROLE_MINIMUMS)
    role_order = [
        "lands",
        "ramp",
        "draw",
        "removal",
        "board_wipe",
        "protection",
        "finisher",
    ]
    roles = [role for role in role_order if role in target_map]
    roles.extend(role for role in target_map if role not in roles)

    for role in roles:
        minimum = target_map[role]
        count = function_counts.get(role_bucket(role), 0)
        quality_count = quality_counts.get(role, 0)
        average_quality = (
            round(quality_totals[role] / quality_count, 2)
            if quality_count and role in quality_totals
            else None
        )
        coverage.append(
            RoleCoverage(
                function=role,
                count=count,
                minimum=minimum,
                average_quality=average_quality,
            )
        )

    return coverage


def _get_suggestions(
    session: Session,
    archetype: str | None,
    bracket: str | None,
    deck_oracle_ids: set[str],
    color_identity: list[str],
    limit: int = 10,
) -> list[dict]:
    if not archetype:
        return []

    rows = session.execute(
        select(
            CardArchetypeScore.oracle_id,
            CardArchetypeScore.score,
            Card.name,
            Card.color_identity,
        )
        .join(Card, Card.oracle_id == CardArchetypeScore.oracle_id)
        .join(
            EDHRecCardStats, EDHRecCardStats.oracle_id == CardArchetypeScore.oracle_id
        )
        .where(CardArchetypeScore.archetype == archetype)
        .where(CardArchetypeScore.score >= 4)
        .where(CardArchetypeScore.oracle_id.notin_(deck_oracle_ids))
        .order_by(CardArchetypeScore.score.desc(), EDHRecCardStats.num_decks.desc())
        .limit(limit * 3)
    ).all()

    suggestions = []
    for row in rows:
        card_colors = set(row.color_identity or [])
        deck_colors = set(color_identity or [])
        if card_colors and not card_colors.issubset(deck_colors | {"C"}):
            continue
        suggestions.append(
            {
                "name": row.name,
                "oracle_id": row.oracle_id,
                "archetype_score": row.score,
            }
        )
        if len(suggestions) >= limit:
            break

    return suggestions


def _cards_by_oracle(session: Session, oracle_ids: list[str]) -> dict[str, Card]:
    if not oracle_ids:
        return {}
    cards = session.scalars(select(Card).where(Card.oracle_id.in_(oracle_ids))).all()
    return {card.oracle_id: card for card in cards}


def _flat_functions(
    parsed_cards: list[ParsedCard],
    classifications: dict[str, dict],
) -> list[str]:
    functions: list[str] = []
    for parsed_card in parsed_cards:
        card_functions = normalize_functions(
            classifications.get(parsed_card.oracle_id, {}).get("functions", [])
        )
        for _ in range(parsed_card.quantity):
            functions.extend(card_functions)
    return functions


def _card_functions_by_name(
    parsed_cards: list[ParsedCard],
    cards_by_oracle: dict[str, Card],
    classifications: dict[str, dict],
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for parsed_card in parsed_cards:
        card = cards_by_oracle.get(parsed_card.oracle_id)
        name = card.name if card else parsed_card.raw_name
        out[name] = normalize_functions(
            classifications.get(parsed_card.oracle_id, {}).get("functions", [])
        )
    return out


def _count_role_cards(
    parsed_cards: list[ParsedCard],
    classifications: dict[str, dict],
    role: str,
) -> int:
    return sum(
        parsed_card.quantity
        for parsed_card in parsed_cards
        if has_function(
            classifications.get(parsed_card.oracle_id, {}).get("functions", []), role
        )
    )


def _count_interaction_cards(
    parsed_cards: list[ParsedCard],
    classifications: dict[str, dict],
) -> int:
    interaction_roles = {"removal", "board_wipe", "counterspell"}
    count = 0
    for parsed_card in parsed_cards:
        functions = normalize_functions(
            classifications.get(parsed_card.oracle_id, {}).get("functions", [])
        )
        if any(role_bucket(fn) in interaction_roles for fn in functions):
            count += parsed_card.quantity
    return count


def _phase5_analysis(
    session: Session,
    parsed: ParsedDecklist,
    classifications: dict[str, dict],
    cards_by_oracle: dict[str, Card],
    archetype: str | None,
    bracket: str | None,
) -> tuple[
    CommanderProfileData | None,
    RoleTargets | None,
    list[RoleCoverage],
    list[str],
    ManaAnalysis | None,
    ConsistencyReport | None,
    PackageHealth | None,
    list[NonboWarning],
]:
    commander_card = (
        cards_by_oracle.get(parsed.commander.oracle_id) if parsed.commander else None
    )
    partner_card = (
        cards_by_oracle.get(parsed.partner.oracle_id) if parsed.partner else None
    )
    resolved_cards = parsed.all_cards

    profile = (
        get_or_generate_profile(session, commander_card) if commander_card else None
    )

    nonland_cards = [
        cards_by_oracle[pc.oracle_id]
        for pc in resolved_cards
        if pc.oracle_id in cards_by_oracle and not cards_by_oracle[pc.oracle_id].is_land
    ]
    avg_cmc = (
        sum(float(card.cmc or 0) for card in nonland_cards) / len(nonland_cards)
        if nonland_cards
        else 3.5
    )

    bracket_int = _BRACKET_INT.get(bracket or "", 3)
    role_targets = compute_role_targets(
        bracket=bracket_int,
        profile=profile,
        archetype=archetype,
        avg_cmc=avg_cmc,
    )
    coverage = _role_coverage(
        resolved_cards,
        classifications,
        cards_by_oracle,
        targets=role_targets,
    )
    gaps = [
        f"Low {rc.function.replace('_', ' ')}: {rc.count}/{rc.minimum} recommended"
        for rc in coverage
        if not rc.meets_minimum
    ]

    land_names = [
        cards_by_oracle[pc.oracle_id].name
        for pc in resolved_cards
        if pc.oracle_id in cards_by_oracle and cards_by_oracle[pc.oracle_id].is_land
        for _ in range(pc.quantity)
    ]
    color_identity = list(commander_card.color_identity or []) if commander_card else []
    mana_analysis = (
        analyze_mana_base(
            land_names=land_names,
            commander_mana_cost=commander_card.mana_cost,
            commander_cmc=float(commander_card.cmc or 3),
            deck_color_identity=color_identity,
            partner_mana_cost=partner_card.mana_cost if partner_card else None,
        )
        if commander_card
        else None
    )

    flat_functions = _flat_functions(resolved_cards, classifications)
    package_health = check_package_health(archetype, flat_functions)
    card_functions_by_name = _card_functions_by_name(
        resolved_cards, cards_by_oracle, classifications
    )

    land_count = len(land_names)
    ramp_count = _count_role_cards(resolved_cards, classifications, "ramp")
    board_wipe_count = _count_role_cards(resolved_cards, classifications, "board_wipe")
    interaction_count = _count_interaction_cards(resolved_cards, classifications)
    creature_count = sum(
        pc.quantity
        for pc in resolved_cards
        if pc.oracle_id in cards_by_oracle and cards_by_oracle[pc.oracle_id].is_creature
    )

    enabler_count = 0
    payoff_count = 0
    pkg = ARCHETYPE_PACKAGES.get(archetype or "")
    if pkg:
        for parsed_card in resolved_cards:
            functions = normalize_functions(
                classifications.get(parsed_card.oracle_id, {}).get("functions", [])
            )
            if any(fn in pkg.enabler_functions for fn in functions):
                enabler_count += parsed_card.quantity
            if any(fn in pkg.payoff_functions for fn in functions):
                payoff_count += parsed_card.quantity

    consistency = (
        compute_consistency(
            land_count=land_count,
            ramp_count=ramp_count,
            interaction_count=interaction_count,
            commander_cmc=float(commander_card.cmc or 3),
            enabler_count=enabler_count,
            payoff_count=payoff_count,
            deck_size=98 if parsed.partner else 99,
        )
        if commander_card
        else None
    )

    nonbos = detect_nonbos(
        archetype=archetype,
        all_functions=flat_functions,
        all_card_names=list(card_functions_by_name),
        all_card_functions=card_functions_by_name,
        ramp_count=ramp_count,
        avg_cmc=avg_cmc,
        land_count=land_count,
        board_wipe_count=board_wipe_count,
        creature_count=creature_count,
    )

    return (
        profile,
        role_targets,
        coverage,
        gaps,
        mana_analysis,
        consistency,
        package_health,
        nonbos,
    )


def evaluate_decklist(
    session: Session,
    parsed: ParsedDecklist,
    deck_name: str = "My Deck",
) -> EvaluationResult:
    now = datetime.utcnow()

    all_oracle_ids = [pc.oracle_id for pc in parsed.all_cards]
    classifications = _get_classifications(session, all_oracle_ids)
    cards_by_oracle = _cards_by_oracle(session, all_oracle_ids)

    classified = {oid for oid, cls in classifications.items() if cls["functions"]}
    unclassified = [
        pc.raw_name for pc in parsed.all_cards if pc.oracle_id not in classified
    ]

    all_card_names = [pc.raw_name for pc in parsed.all_cards]
    all_oracle_id_set = set(pc.oracle_id for pc in parsed.all_cards)
    game_changers_found = _count_game_changers(all_card_names)

    # Hard deterministic signals
    combos_found = _find_combos(session, all_oracle_id_set)
    mass_land_denial_found = [n for n in all_card_names if n in MASS_LAND_DENIAL]
    extra_turns_found = [n for n in all_card_names if n in EXTRA_TURN_CARDS]

    archetype = _guess_archetype(classifications)
    synergy = _compute_synergy_score(classifications, archetype)
    coverage = _role_coverage(parsed.all_cards, classifications, cards_by_oracle)
    gaps = [
        f"Low {rc.function.replace('_', ' ')}: {rc.count}/{rc.minimum} recommended"
        for rc in coverage
        if not rc.meets_minimum
    ]

    commander_card = (
        cards_by_oracle.get(parsed.commander.oracle_id) if parsed.commander else None
    )
    color_identity = list(commander_card.color_identity or []) if commander_card else []

    # LLM bracket classification — hard floors enforced inside classify_bracket.
    # Fall back to the deterministic estimate if the LLM call fails.
    try:
        bracket_result: BracketResult = classify_bracket(
            commander=parsed.commander.raw_name if parsed.commander else "Unknown",
            partner=parsed.partner.raw_name if parsed.partner else None,
            archetype=archetype,
            game_changers_found=game_changers_found,
            combos_found=combos_found,
            mass_land_denial_found=mass_land_denial_found,
            extra_turns_found=extra_turns_found,
            role_coverage={rc.function: rc.count for rc in coverage},
            card_names=all_card_names,
        )
        bracket = bracket_result.bracket_label
        bracket_reasoning = bracket_result.reasoning
    except Exception:
        # Deterministic fallback: WotC bracket rules based on Game Changers count
        bracket, _gc_count = _estimate_bracket(game_changers_found, classifications)
        bracket_reasoning = (
            f"LLM unavailable — estimated from {len(game_changers_found)} "
            f"Game Changer(s) found in deck."
        )

    (
        commander_profile,
        role_targets,
        coverage,
        gaps,
        mana_analysis,
        consistency,
        package_health,
        nonbo_warnings,
    ) = _phase5_analysis(
        session=session,
        parsed=parsed,
        classifications=classifications,
        cards_by_oracle=cards_by_oracle,
        archetype=archetype,
        bracket=bracket,
    )

    suggestions = _get_suggestions(
        session,
        archetype,
        bracket,
        deck_oracle_ids=set(all_oracle_ids),
        color_identity=color_identity,
    )

    # Persist decklist
    decklist = Decklist(
        name=deck_name,
        commander_oracle_id=parsed.commander.oracle_id if parsed.commander else None,
        partner_oracle_id=parsed.partner.oracle_id if parsed.partner else None,
        colors=color_identity,
        created_at=now,
        raw_list=None,
    )
    session.add(decklist)
    session.flush()

    for pc in parsed.all_cards:
        session.add(
            DeckCard(
                decklist_id=decklist.id,
                oracle_id=pc.oracle_id,
                quantity=pc.quantity,
                is_commander=pc.is_commander,
            )
        )

    db_eval = DeckEvaluation(
        decklist_id=decklist.id,
        evaluated_at=now,
        bracket_estimate=bracket,
        archetype_guess=archetype,
        synergy_score=synergy,
        legality_issues={},
        structural_notes={
            "role_coverage": {rc.function: rc.count for rc in coverage},
            "role_targets": role_targets.to_dict() if role_targets else {},
            "role_quality": {
                rc.function: rc.average_quality
                for rc in coverage
                if rc.average_quality is not None
            },
            "gaps": gaps,
            "unclassified_count": len(unclassified),
            "game_changers": game_changers_found,
            "bracket_reasoning": bracket_reasoning,
            "mana_analysis": mana_analysis.to_dict() if mana_analysis else None,
            "consistency": consistency.to_dict() if consistency else None,
            "package_health": package_health.to_dict() if package_health else None,
            "nonbos": [warning.to_dict() for warning in nonbo_warnings],
        },
        improvement_suggestions={"suggestions": suggestions},
    )
    session.add(db_eval)
    session.flush()

    return EvaluationResult(
        decklist_id=decklist.id,
        commander_name=parsed.commander.raw_name if parsed.commander else None,
        archetype_guess=archetype,
        bracket_estimate=bracket,
        bracket_reasoning=bracket_reasoning,
        game_changers_found=game_changers_found,
        combos_found=combos_found,
        mass_land_denial_found=mass_land_denial_found,
        extra_turns_found=extra_turns_found,
        synergy_score=synergy,
        role_coverage=coverage,
        gaps=gaps,
        unclassified_cards=unclassified,
        unresolved_cards=parsed.unresolved,
        improvement_suggestions=suggestions,
        commander_profile=commander_profile,
        role_targets=role_targets,
        mana_analysis=mana_analysis,
        consistency=consistency,
        package_health=package_health,
        nonbo_warnings=nonbo_warnings,
    )
