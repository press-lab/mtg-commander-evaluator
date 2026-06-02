from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from mtg_evaluator.card_functions import has_function, normalize_functions, role_bucket
from mtg_evaluator.db.models import Card, RawScryfallCard
from mtg_evaluator.deckbuilding.commander_profile import (
    CommanderProfileData,
    get_or_generate_profile,
)
from mtg_evaluator.deckbuilding.consistency import (
    ConsistencyReport,
    compute_consistency,
)
from mtg_evaluator.deckbuilding.mana_analysis import (
    LandManaData,
    ManaAnalysis,
    analyze_mana_base,
)
from mtg_evaluator.deckbuilding.packages import (
    ARCHETYPE_PACKAGES,
    NonboWarning,
    PackageHealth,
    check_package_health,
    detect_nonbos,
)
from mtg_evaluator.deckbuilding.role_quality import compute_role_quality
from mtg_evaluator.deckbuilding.role_targets import RoleTargets, compute_role_targets
from mtg_evaluator.evaluation.game_changers import GAME_CHANGERS
from mtg_evaluator.evaluation.parser import ParsedCard, ParsedDecklist

_BRACKET_INT = {
    "casual": 1,
    "bracket_1": 1,
    "bracket_2": 2,
    "bracket_3": 3,
    "bracket_4": 4,
    "cedh": 5,
}

# Fallback only, used when commander-aware targets cannot be computed.
_ROLE_MINIMUMS = {
    "ramp": 10,
    "draw": 8,
    "removal": 6,
    "board_wipe": 2,
    "protection": 2,
}


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
class DeckAnalysis:
    commander_profile: CommanderProfileData | None
    role_targets: RoleTargets | None
    role_coverage: list[RoleCoverage]
    gaps: list[str]
    mana_analysis: ManaAnalysis | None
    consistency: ConsistencyReport | None
    package_health: PackageHealth | None
    nonbo_warnings: list[NonboWarning] = field(default_factory=list)


def analyze_actual_deck(
    session: Session,
    parsed: ParsedDecklist,
    classifications: dict[str, dict],
    cards_by_oracle: dict[str, Card],
    archetype: str | None,
    bracket: str | None,
) -> DeckAnalysis:
    """Run commander-aware analysis on the actual uploaded decklist."""
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

    role_targets = None
    if commander_card:
        role_targets = compute_role_targets(
            bracket=_BRACKET_INT.get(bracket or "", 3),
            profile=profile,
            archetype=archetype,
            avg_cmc=avg_cmc,
        )

    coverage = role_coverage(
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
    mana_analysis = None
    if commander_card:
        mana_analysis = analyze_mana_base(
            land_names=land_names,
            commander_mana_cost=commander_card.mana_cost,
            commander_cmc=float(commander_card.cmc or 3),
            deck_color_identity=list(commander_card.color_identity or []),
            partner_mana_cost=partner_card.mana_cost if partner_card else None,
            land_metadata=_land_mana_metadata(session, cards_by_oracle),
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

    enabler_count, payoff_count = _package_counts(
        resolved_cards, classifications, archetype
    )
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

    return DeckAnalysis(
        commander_profile=profile,
        role_targets=role_targets,
        role_coverage=coverage,
        gaps=gaps,
        mana_analysis=mana_analysis,
        consistency=consistency,
        package_health=package_health,
        nonbo_warnings=nonbos,
    )


def phase5_structure_score(result, total_cards: int) -> float:
    """
    V1 structure score for pasted deck evaluation.

    This uses only Phase 5 structural analysis and cleanliness signals; bracket
    power, EDHREC lift, and ML-derived scoring are intentionally excluded.
    """
    coverages = list(result.role_coverage or [])
    if coverages:
        coverage_scores = [
            min(1.0, rc.count / rc.minimum) if rc.minimum else 1.0 for rc in coverages
        ]
        role_coverage_pct = sum(coverage_scores) / len(coverage_scores)
        role_quality_values = [
            rc.average_quality / 5.0
            for rc in coverages
            if rc.average_quality is not None
        ]
        role_quality_pct = (
            sum(role_quality_values) / len(role_quality_values)
            if role_quality_values
            else 0.6
        )
    else:
        role_coverage_pct = 0.0
        role_quality_pct = 0.6

    mana_pct = result.mana_analysis.cast_reliability if result.mana_analysis else 0.6
    consistency_pct = result.consistency.overall_score if result.consistency else 0.6

    package = result.package_health
    if not package:
        package_pct = 1.0
    else:
        enabler_pct = (
            min(1.0, package.enabler_count / package.min_enablers)
            if package.min_enablers
            else 1.0
        )
        payoff_pct = (
            min(1.0, package.payoff_count / package.min_payoffs)
            if package.min_payoffs
            else 1.0
        )
        package_pct = (enabler_pct + payoff_pct) / 2

    severity_penalty = {"high": 0.08, "medium": 0.05, "low": 0.025}
    nonbo_penalty = min(
        0.25,
        sum(
            severity_penalty.get(warning.severity, 0.04)
            for warning in result.nonbo_warnings
        ),
    )
    clean_penalty = min(
        0.20,
        (len(result.unclassified_cards) + len(result.unresolved_cards))
        / max(total_cards, 1),
    )

    raw = (
        role_coverage_pct * 0.30
        + role_quality_pct * 0.15
        + mana_pct * 0.20
        + consistency_pct * 0.20
        + package_pct * 0.15
    )
    return round(max(0.0, min(1.0, raw - nonbo_penalty - clean_penalty)) * 100, 1)


def phase5_score_fields(result, total_cards: int) -> dict[str, float]:
    """Return summary score fields for API compatibility."""
    structure_score = phase5_structure_score(result, total_cards)
    return {
        "deck_score": structure_score,
        "structure_score": structure_score,
    }


def role_coverage(
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
            if card and role != "lands":
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

    coverage = []
    for role in roles:
        quality_count = quality_counts.get(role, 0)
        average_quality = (
            round(quality_totals[role] / quality_count, 2)
            if quality_count and role in quality_totals
            else None
        )
        coverage.append(
            RoleCoverage(
                function=role,
                count=function_counts.get(role_bucket(role), 0),
                minimum=target_map[role],
                average_quality=average_quality,
            )
        )
    return coverage


def _land_mana_metadata(
    session: Session,
    cards_by_oracle: dict[str, Card],
) -> dict[str, LandManaData]:
    land_cards = [card for card in cards_by_oracle.values() if card.is_land]
    metadata = {
        card.name: LandManaData(oracle_text=card.oracle_text or "")
        for card in land_cards
    }
    if not land_cards:
        return metadata

    raw_rows = session.execute(
        select(RawScryfallCard.oracle_id, RawScryfallCard.raw_json)
        .where(RawScryfallCard.oracle_id.in_([card.oracle_id for card in land_cards]))
        .order_by(RawScryfallCard.ingested_at.desc())
    ).all()
    cards_by_id = {card.oracle_id: card for card in land_cards}
    seen: set[str] = set()
    for oracle_id, raw_json in raw_rows:
        if oracle_id in seen:
            continue
        seen.add(oracle_id)
        card = cards_by_id.get(oracle_id)
        if not card:
            continue
        metadata[card.name] = LandManaData(
            produced_mana=tuple(raw_json.get("produced_mana") or ()),
            oracle_text=raw_json.get("oracle_text") or card.oracle_text or "",
        )
    return metadata


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


def _package_counts(
    parsed_cards: list[ParsedCard],
    classifications: dict[str, dict],
    archetype: str | None,
) -> tuple[int, int]:
    enabler_count = 0
    payoff_count = 0
    pkg = ARCHETYPE_PACKAGES.get(archetype or "")
    if not pkg:
        return enabler_count, payoff_count
    for parsed_card in parsed_cards:
        functions = normalize_functions(
            classifications.get(parsed_card.oracle_id, {}).get("functions", [])
        )
        if any(fn in pkg.enabler_functions for fn in functions):
            enabler_count += parsed_card.quantity
        if any(fn in pkg.payoff_functions for fn in functions):
            payoff_count += parsed_card.quantity
    return enabler_count, payoff_count
