from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mtg_evaluator.card_functions import (
    normalize_function,
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
from mtg_evaluator.evaluation.parser import ParsedDecklist
from mtg_evaluator.evaluation.bracket_llm import classify_bracket, BracketResult
from mtg_evaluator.evaluation.game_changers import is_game_changer
from mtg_evaluator.evaluation.deck_analysis import (
    RoleCoverage,
    analyze_actual_deck,
    role_coverage,
)
from mtg_evaluator.evaluation.recommendations import (
    RecommendationReport,
    build_recommendations,
)
from mtg_evaluator.db.models import SpellbookCombo, SpellbookComboCard
from mtg_evaluator.deckbuilding.commander_profile import (
    CommanderProfileData,
)
from mtg_evaluator.deckbuilding.consistency import ConsistencyReport
from mtg_evaluator.deckbuilding.mana_analysis import (
    ManaAnalysis,
)
from mtg_evaluator.deckbuilding.packages import (
    PackageHealth,
    NonboWarning,
)
from mtg_evaluator.deckbuilding.role_targets import RoleTargets

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


@dataclass
class ComboHit:
    spellbook_id: str
    bracket_tag: str  # R/S/P/O/C/E
    card_names: list[str]
    results_description: str | None


_COMBO_CARD_SET_CACHE: dict[str, frozenset[str]] = {}
_COMBO_CARD_NAME_CACHE: dict[str, tuple[tuple[str, str], ...]] = {}


def _combo_card_sets(
    session: Session,
    combo_ids: list[str],
) -> dict[str, frozenset[str]]:
    """Return cached oracle-id sets for the requested Spellbook combos."""
    missing = [
        combo_id for combo_id in combo_ids if combo_id not in _COMBO_CARD_SET_CACHE
    ]
    if missing:
        rows = session.execute(
            select(
                SpellbookComboCard.combo_id,
                SpellbookComboCard.oracle_id,
                SpellbookComboCard.card_name,
            )
            .where(SpellbookComboCard.combo_id.in_(missing))
            .where(SpellbookComboCard.oracle_id.isnot(None))
        ).all()
        grouped_oracles: dict[str, set[str]] = {combo_id: set() for combo_id in missing}
        grouped_names: dict[str, list[tuple[str, str]]] = {
            combo_id: [] for combo_id in missing
        }
        for combo_id, oracle_id, card_name in rows:
            grouped_oracles.setdefault(combo_id, set()).add(oracle_id)
            grouped_names.setdefault(combo_id, []).append((oracle_id, card_name))
        for combo_id in missing:
            _COMBO_CARD_SET_CACHE[combo_id] = frozenset(
                grouped_oracles.get(combo_id, set())
            )
            _COMBO_CARD_NAME_CACHE[combo_id] = tuple(grouped_names.get(combo_id, []))

    return {combo_id: _COMBO_CARD_SET_CACHE[combo_id] for combo_id in combo_ids}


def _find_combos(session: Session, oracle_ids: set[str]) -> list[ComboHit]:
    """Return all Spellbook combos where every card's oracle_id is in the deck."""
    if not oracle_ids:
        return []

    candidate_ids = list(
        session.scalars(
            select(SpellbookComboCard.combo_id)
            .where(SpellbookComboCard.oracle_id.in_(oracle_ids))
            .distinct()
        ).all()
    )
    if not candidate_ids:
        return []

    combo_oracles = _combo_card_sets(session, candidate_ids)
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
        card_names = [
            name
            for oracle_id, name in _COMBO_CARD_NAME_CACHE.get(combo.spellbook_id, ())
            if oracle_id in oracle_ids
        ]
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
        if is_game_changer(name):
            found.append(name)
    return found


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
    recommendations: RecommendationReport | None = None


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
    coverage = role_coverage(parsed.all_cards, classifications, cards_by_oracle)
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

    analysis = analyze_actual_deck(
        session=session,
        parsed=parsed,
        classifications=classifications,
        cards_by_oracle=cards_by_oracle,
        archetype=archetype,
        bracket=bracket,
    )
    commander_profile = analysis.commander_profile
    role_targets = analysis.role_targets
    coverage = analysis.role_coverage
    gaps = analysis.gaps
    mana_analysis = analysis.mana_analysis
    consistency = analysis.consistency
    package_health = analysis.package_health
    nonbo_warnings = analysis.nonbo_warnings

    # Recommendation engine: missing staples, gap fillers, cut candidates
    partner_card = (
        cards_by_oracle.get(parsed.partner.oracle_id) if parsed.partner else None
    )
    role_gaps = {rc.function: rc.gap for rc in coverage}
    combo_oracle_ids: set[str] = set()
    for hit in combos_found:
        combo_oracle_ids |= (
            _COMBO_CARD_SET_CACHE.get(hit.spellbook_id, frozenset())
            & all_oracle_id_set
        )

    recommendations = build_recommendations(
        session=session,
        parsed_cards=parsed.all_cards,
        classifications=classifications,
        cards_by_oracle=cards_by_oracle,
        commander=commander_card,
        partner=partner_card,
        archetype=archetype,
        bracket_int=_BRACKET_INT.get(bracket or "", 3),
        role_gaps=role_gaps,
        combo_oracle_ids=combo_oracle_ids,
        mass_land_denial=MASS_LAND_DENIAL,
    )

    # Flat list kept for backward compatibility — staples first, then archetype picks
    suggestions = [
        {
            "name": r.name,
            "oracle_id": r.oracle_id,
            "reason": r.reason,
            "inclusion_rate": r.inclusion_rate,
        }
        for r in recommendations.staples_missing
    ]
    if not suggestions:
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
        improvement_suggestions={
            "suggestions": suggestions,
            "recommendations": recommendations.to_dict(),
        },
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
        recommendations=recommendations,
    )
