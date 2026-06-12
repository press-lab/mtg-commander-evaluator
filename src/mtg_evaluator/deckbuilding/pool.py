"""
Card pool builder — Phase 5 (commander-aware deck intelligence).

Pipeline:
  1. Resolve commander(s), fetch or generate commander profile
  2. Compute dynamic role targets (commander + archetype + bracket + curve)
  3. Load all classified cards in color identity
  4. Score each card with the revised composite formula:
       commander_fit + archetype_fit + role_need * role_quality
       + package_synergy + bracket_fit + combo_signal
       + popularity_floor - redundancy_penalty - mana_penalty
  5. Tier assignment (CORE / SUPPORT / FLEX)
  6. Pool-level package health and nonbo checks
  7. Consistency math (hypergeometric)

Card tiers:
  CORE    — arch ≥ 4 AND bracket ≥ 3, OR in a legal combo
  SUPPORT — arch ≥ 3 OR fills a needed role
  FLEX    — everything else that passes color + bracket filter
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from mtg_evaluator.card_functions import has_function, normalize_functions, role_bucket
from mtg_evaluator.db.models import (
    Card,
    CardClassification,
    CardArchetypeScore,
    CardBracketScore,
    CardFunction,
    EDHRecCardStats,
    SpellbookCombo,
    SpellbookComboCard,
)
from mtg_evaluator.evaluation.game_changers import GAME_CHANGERS
from mtg_evaluator.deckbuilding.request import DeckRequest, SALT_THRESHOLDS
from mtg_evaluator.deckbuilding.archetypes import normalize_archetype
from mtg_evaluator.deckbuilding.lands import land_score
from mtg_evaluator.deckbuilding.commander_profile import (
    CommanderProfileData,
    get_or_generate_profile,
)
from mtg_evaluator.deckbuilding.role_quality import (
    need_weighted_role_quality,
    role_quality_map,
)
from mtg_evaluator.deckbuilding.role_targets import (
    RoleTargets,
    compute_role_targets,
    saturation_multiplier,
)
from mtg_evaluator.deckbuilding.consistency import (
    compute_consistency,
    ConsistencyReport,
)
from mtg_evaluator.deckbuilding.packages import (
    check_package_health,
    detect_nonbos,
    PackageHealth,
    NonboWarning,
)

Tier = Literal["CORE", "COMBO", "SUPPORT", "FLEX"]

_BRACKET_KEY = {1: "casual", 2: "bracket_2", 3: "bracket_3", 4: "bracket_4", 5: "cedh"}

# Roles counted for interaction (removal + board wipes + counters)
_INTERACTION_ROLES = {"removal", "board_wipe", "counterspell"}
# Roles that count toward "needed role" scoring
_NEEDED_ROLES = {"ramp", "draw", "removal", "board_wipe", "protection", "tutor"}


@dataclass
class PoolCard:
    name: str
    oracle_id: str
    tier: Tier
    score: float  # 0-100 composite
    archetype_score: Optional[float]
    bracket_score: Optional[float]
    role_quality: float  # 0-5 need-weighted aggregate role score
    functions: list[str]
    is_game_changer: bool
    combo_ids: list[str]
    edhrec_decks: Optional[int]
    type_line: str
    cmc: float
    role_quality_by_role: dict[str, float] = field(default_factory=dict)
    oracle_text: str = ""
    produced_mana: list[str] = field(default_factory=list)
    price_usd: Optional[float] = None
    salt_score: Optional[float] = None
    # Per-commander EDHREC signals (None when no data for this commander)
    commander_inclusion: Optional[float] = None  # 0-1 fraction of decks
    commander_synergy: Optional[float] = None  # can be negative


@dataclass
class ComboSummary:
    spellbook_id: str
    bracket_tag: str
    card_names: list[str]
    results_description: Optional[str]


@dataclass
class CardPool:
    request: DeckRequest
    commander_name: str
    color_identity: list[str]
    archetype: Optional[str]
    commander_mana_cost: Optional[str] = None
    commander_cmc: float = 3.0
    partner_mana_cost: Optional[str] = None
    partner_cmc: Optional[float] = None

    core: list[PoolCard] = field(default_factory=list)
    combos: list[ComboSummary] = field(default_factory=list)
    support: list[PoolCard] = field(default_factory=list)
    flex: list[PoolCard] = field(default_factory=list)

    # Phase 5 additions
    commander_profile: Optional[CommanderProfileData] = None
    role_targets: Optional[RoleTargets] = None
    consistency: Optional[ConsistencyReport] = None
    package_health: Optional[PackageHealth] = None
    nonbo_warnings: list[NonboWarning] = field(default_factory=list)
    # Per-commander EDHREC data used during scoring (name → inclusion/synergy)
    commander_stats: dict[str, dict] = field(default_factory=dict)

    @property
    def all_cards(self) -> list[PoolCard]:
        return self.core + self.support + self.flex

    @property
    def total(self) -> int:
        return len(self.all_cards)


def _log_popularity(num_decks: Optional[int]) -> float:
    if not num_decks or num_decks <= 0:
        return 0.0
    return min(math.log10(num_decks) / 5.0, 1.0)


def _composite_score(
    archetype_score: Optional[float],
    bracket_score: Optional[float],
    role_quality: float,  # 0-5 (was binary fills_role)
    in_combo: bool,
    popularity: float,
    commander_fit: float = 0.0,  # 0-1 bonus from commander profile
    package_bonus: float = 0.0,  # 0-1 bonus from package membership
) -> float:
    """
    Revised scoring formula (Phase 5):
      commander_fit × 15   — does this card directly support what the commander does?
      archetype_fit × 30   — LLM archetype score (was 40)
      role_need_quality × 25 — role quality score (was binary 20)
      package_synergy × 10 — completes or extends an archetype package
      bracket_fit × 10     — appropriate power level (was 20)
      combo_signal × 7     — part of a legal combo
      popularity × 3       — global EDHREC popularity (low-weight floor)
    """
    a = (archetype_score or 0) / 5.0  # 0-1
    b = (bracket_score or 0) / 5.0  # 0-1
    rq = role_quality / 5.0  # 0-1 (quality-weighted role value)
    c = 1.0 if in_combo else 0.0
    p = popularity  # 0-1
    cf = min(commander_fit, 1.0)
    pk = min(package_bonus, 1.0)

    return round(
        cf * 15 + a * 30 + rq * 25 + pk * 10 + b * 10 + c * 7 + p * 3,
        2,
    )


def _commander_fit_bonus(
    functions: list[str],
    profile: Optional[CommanderProfileData],
) -> float:
    """
    0-1 bonus: how well does this card support the commander's specific needs?
    Cards that fill needs the commander creates score higher.
    """
    if not profile:
        return 0.0

    score = 0.0
    fn_set = set(normalize_functions(functions))

    # Commander needs creatures → creatures score higher
    if profile.needs_creatures and (
        "creature" in fn_set or has_function(fn_set, "token_maker")
    ):
        score += 0.4
    # Commander needs artifacts
    if profile.needs_artifacts and "artifact" in fn_set:
        score += 0.4
    # Commander needs spells
    if profile.needs_spells and any(
        f in fn_set for f in ("instant", "sorcery", "cantrip", "spells")
    ):
        score += 0.4
    # Commander needs combat support
    if profile.needs_combat and any(
        f in fn_set for f in ("evasion", "haste", "pump", "combat_trick")
    ):
        score += 0.4
    # Commander needs attack/damage triggers — evasion + haste are very valuable
    if profile.needs_attack_damage_triggers and any(
        f in fn_set for f in ("evasion", "haste", "unblockable")
    ):
        score += 0.5
    # High dependency score → protection is extra valuable
    if profile.dependency_score >= 3 and any(
        f in fn_set for f in ("protection", "hexproof", "indestructible")
    ):
        score += 0.3

    return min(score, 1.0)


def _package_bonus(
    functions: list[str],
    archetype: Optional[str],
) -> float:
    """
    0-1 bonus for cards that are core to the archetype package (enablers or payoffs).
    Supplements the archetype score for cards that might not be classified yet.
    """
    if not archetype:
        return 0.0

    from mtg_evaluator.deckbuilding.packages import ARCHETYPE_PACKAGES

    pkg = ARCHETYPE_PACKAGES.get(archetype)
    if not pkg:
        return 0.0

    fn_set = set(normalize_functions(functions))
    if any(f in fn_set for f in pkg.enabler_functions):
        return 0.7
    if any(f in fn_set for f in pkg.payoff_functions):
        return 0.7
    if any(f in fn_set for f in pkg.support_functions):
        return 0.3
    return 0.0


def build_card_pool(session: Session, request: DeckRequest) -> CardPool:
    """
    Main entry point. Phase 5: commander-aware, role-quality scoring.
    """
    # --- Resolve commander ---
    commander = session.scalars(
        select(Card).where(Card.name == request.commander_name)
    ).first()
    if not commander:
        raise ValueError(f"Commander '{request.commander_name}' not found in database.")

    # --- Resolve partner ---
    partner_card: Optional[Card] = None
    if request.partner_name:
        partner_card = session.scalars(
            select(Card).where(Card.name == request.partner_name)
        ).first()
        if not partner_card:
            raise ValueError(f"Partner '{request.partner_name}' not found in database.")

    if partner_card:
        from mtg_evaluator.deckbuilding.partners import combined_color_identity

        color_identity = combined_color_identity(commander, partner_card)
    else:
        color_identity = list(commander.color_identity or [])

    # --- Normalize archetype ---
    canonical_archetype = normalize_archetype(request.archetype)
    bracket_key = _BRACKET_KEY.get(request.bracket, "bracket_3")

    partner_display = (
        f"{commander.name} + {partner_card.name}" if partner_card else commander.name
    )

    # --- Commander profile (Phase 5) ---
    profile = get_or_generate_profile(session, commander)

    # No archetype requested or detected → fall back to the profile's
    # preferred archetype so the 30-point archetype component isn't zeroed.
    if not canonical_archetype and profile.preferred_archetypes:
        canonical_archetype = normalize_archetype(profile.preferred_archetypes[0])

    # --- Per-commander EDHREC inclusion data (optional signal) ---
    from mtg_evaluator.ingestion.edhrec_commander import get_or_fetch_commander_stats

    try:
        cmd_stats = get_or_fetch_commander_stats(session, commander, partner_card)
    except Exception:
        cmd_stats = {}

    pool = CardPool(
        request=request,
        commander_name=partner_display,
        color_identity=color_identity,
        archetype=canonical_archetype,
        commander_mana_cost=commander.mana_cost,
        commander_cmc=float(commander.cmc or 3),
        partner_mana_cost=partner_card.mana_cost if partner_card else None,
        partner_cmc=float(partner_card.cmc or 0) if partner_card else None,
        commander_profile=profile,
        commander_stats=cmd_stats,
    )

    # --- Legal combos ---
    legal_combo_ids: set[str] = set()
    combo_oracle_sets: dict[str, set[str]] = {}

    if request.want_combos and request.allowed_combo_tags:
        combo_rows = session.execute(
            select(
                SpellbookCombo.spellbook_id,
                SpellbookCombo.bracket_tag,
                SpellbookCombo.results_description,
            ).where(SpellbookCombo.bracket_tag.in_(request.allowed_combo_tags))
        ).all()

        for combo_id, btag, desc in combo_rows:
            card_rows = session.execute(
                select(SpellbookComboCard.oracle_id, SpellbookComboCard.card_name)
                .where(SpellbookComboCard.combo_id == combo_id)
                .where(SpellbookComboCard.oracle_id.isnot(None))
            ).all()

            if not card_rows:
                continue

            oracle_ids = [r.oracle_id for r in card_rows]
            card_names_map = {r.oracle_id: r.card_name for r in card_rows}

            combo_cards = session.execute(
                select(Card.oracle_id, Card.color_identity).where(
                    Card.oracle_id.in_(oracle_ids)
                )
            ).all()

            deck_colors = set(color_identity) | {"C"}
            fits = all(
                set(c.color_identity or []).issubset(deck_colors) for c in combo_cards
            )
            if not fits:
                continue

            legal_combo_ids.add(combo_id)
            combo_oracle_sets[combo_id] = set(oracle_ids)
            pool.combos.append(
                ComboSummary(
                    spellbook_id=combo_id,
                    bracket_tag=btag or "",
                    card_names=[card_names_map[oid] for oid in oracle_ids],
                    results_description=desc,
                )
            )

    combo_oracle_union: set[str] = (
        set().union(*combo_oracle_sets.values()) if combo_oracle_sets else set()
    )
    preliminary_role_targets = compute_role_targets(
        bracket=request.bracket,
        profile=profile,
        archetype=canonical_archetype,
        avg_cmc=3.5,
    )

    # --- Load all classified cards in color identity ---
    cls_subq = (
        select(CardClassification.id, CardClassification.oracle_id)
        .where(CardClassification.is_valid == True)  # noqa: E712
        .order_by(CardClassification.oracle_id, CardClassification.classified_at.desc())
        .distinct(CardClassification.oracle_id)
        .subquery()
    )

    rows = session.execute(
        select(
            Card.oracle_id,
            Card.name,
            Card.type_line,
            Card.color_identity,
            Card.cmc,
            Card.is_instant,
            Card.is_sorcery,
            Card.oracle_text,
            Card.price_usd,
            CardArchetypeScore.score.label("arch_score"),
            CardBracketScore.score.label("brack_score"),
            EDHRecCardStats.num_decks,
            EDHRecCardStats.salt_score,
        )
        .join(cls_subq, cls_subq.c.oracle_id == Card.oracle_id)
        .join(
            CardArchetypeScore,
            (CardArchetypeScore.classification_id == cls_subq.c.id)
            & (CardArchetypeScore.archetype == (canonical_archetype or "")),
            isouter=True,
        )
        .join(
            CardBracketScore,
            (CardBracketScore.classification_id == cls_subq.c.id)
            & (CardBracketScore.bracket_level == bracket_key),
            isouter=True,
        )
        .join(
            EDHRecCardStats, EDHRecCardStats.oracle_id == Card.oracle_id, isouter=True
        )
        .where(Card.oracle_id != commander.oracle_id)
        .where(Card.oracle_id != partner_card.oracle_id if partner_card else True)
    ).all()

    # Build function map
    cls_ids_by_oracle: dict[str, str] = {}
    for row in session.execute(select(cls_subq)).all():
        cls_ids_by_oracle[row.oracle_id] = row.id

    fn_map: dict[str, list[str]] = {}
    if cls_ids_by_oracle:
        fn_rows = session.execute(
            select(CardFunction.classification_id, CardFunction.function_name).where(
                CardFunction.classification_id.in_(cls_ids_by_oracle.values())
            )
        ).all()
        cls_to_oracle = {v: k for k, v in cls_ids_by_oracle.items()}
        for fn_row in fn_rows:
            oid = cls_to_oracle.get(fn_row.classification_id)
            if oid:
                fn_map.setdefault(oid, []).append(fn_row.function_name)

    deck_colors_set = set(color_identity) | {"C"}
    tutor_multiplier = {
        "none": 0.5,
        "light": 1.0,
        "heavy": 1.3,
    }.get(request.tutor_density, 1.0)
    salt_cap = SALT_THRESHOLDS.get(request.salt_tolerance)

    all_scored: list[PoolCard] = []

    for row in rows:
        # Color identity filter
        card_colors = set(row.color_identity or [])
        if card_colors and not card_colors.issubset(deck_colors_set):
            continue

        # Game Changer bracket filter
        is_gc = row.name in GAME_CHANGERS
        if is_gc and request.bracket < 3:
            continue

        # Budget filter — unknown prices pass (almost always bulk cards)
        price = float(row.price_usd) if row.price_usd is not None else None
        if (
            request.max_card_price is not None
            and price is not None
            and price > request.max_card_price
        ):
            continue

        # Salt filter — unknown salt passes (only ~top salty cards are scored)
        salt = float(row.salt_score) if row.salt_score is not None else None
        if salt_cap is not None and salt is not None and salt > salt_cap:
            continue

        functions = normalize_functions(fn_map.get(row.oracle_id, []))
        in_combo = row.oracle_id in combo_oracle_union
        popularity = _log_popularity(row.num_decks)

        is_land = "land" in row.type_line.lower()
        cmc = float(row.cmc or 0)
        oracle_text = row.oracle_text or ""

        if is_land:
            # Land quality tier scoring overrides composite
            ls = land_score(row.name, request.bracket)
            score = (
                ls
                if ls is not None
                else _composite_score(
                    row.arch_score,
                    row.brack_score,
                    0.0,
                    in_combo,
                    popularity,
                )
            )
            rq = 0.0  # lands don't have a role quality
            rq_by_role = {}
        else:
            # Role quality (per-role, then need-weighted for composite scoring)
            fills_needed_role = any(
                role_bucket(fn) in _NEEDED_ROLES for fn in functions
            )
            rq_by_role = role_quality_map(
                functions,
                cmc,
                bool(row.is_instant),
                bool(row.is_sorcery),
                is_gc,
                oracle_text,
            )
            rq = (
                need_weighted_role_quality(
                    {
                        role: quality
                        for role, quality in rq_by_role.items()
                        if role in _NEEDED_ROLES
                    },
                    preliminary_role_targets.to_dict(),
                )
                if fills_needed_role
                else 0.0
            )

            # Commander fit and package bonuses
            cf = _commander_fit_bonus(functions, profile)
            pk = _package_bonus(functions, canonical_archetype)

            score = _composite_score(
                row.arch_score,
                row.brack_score,
                rq,
                in_combo,
                popularity,
                commander_fit=cf,
                package_bonus=pk,
            )

            # Tutor density modifier
            is_tutor = has_function(functions, "tutor")
            if is_tutor:
                score = round(score * tutor_multiplier, 2)

        # Per-commander EDHREC affinity (non-lands only — lands keep their
        # tier-based scores). How strongly consensus pulls depends on the
        # requested build style:
        #   optimized — converge on the meta build (heavy inclusion/synergy pull)
        #   balanced  — moderate signal
        #   spicy     — consensus is a *penalty*; underplayed cards that still
        #               grade well on role quality / archetype fit get a boost
        cmd_stat = cmd_stats.get(row.name) or cmd_stats.get(
            row.name.split(" // ")[0]
        )
        cmd_inclusion = cmd_stat["inclusion_rate"] if cmd_stat else None
        cmd_synergy = cmd_stat.get("synergy") if cmd_stat else None
        if not is_land:
            style = request.build_style
            if cmd_stat and style == "optimized":
                # Consensus must dominate: a 60%+ inclusion staple should
                # comfortably clear the pool trim line on affinity alone.
                affinity = (cmd_inclusion or 0.0) * 30 + max(
                    0.0, cmd_synergy or 0.0
                ) * 25
                score = round(min(100.0, score + affinity), 2)
            elif cmd_stat and style == "balanced":
                affinity = (cmd_inclusion or 0.0) * 8 + max(
                    0.0, cmd_synergy or 0.0
                ) * 12
                score = round(min(100.0, score + affinity), 2)
            elif style == "spicy":
                if cmd_stat and (cmd_inclusion or 0.0) > 0.30:
                    # Heavily-played card — dock it so hidden gems can pass
                    score = round(score * (1 - 0.3 * min(cmd_inclusion, 0.8)), 2)
                elif (
                    not cmd_stat
                    and popularity < 0.5
                    and ((row.arch_score or 0) >= 3 or rq >= 3.0)
                ):
                    # Off-meta card that still grades well — the spice
                    score = round(min(100.0, score + 6), 2)

        combo_ids_for_card = [
            cid
            for cid, oracles in combo_oracle_sets.items()
            if row.oracle_id in oracles
        ]

        card = PoolCard(
            name=row.name,
            oracle_id=row.oracle_id,
            tier="FLEX",
            score=score,
            archetype_score=row.arch_score,
            bracket_score=row.brack_score,
            role_quality=rq,
            functions=functions,
            is_game_changer=is_gc,
            combo_ids=combo_ids_for_card,
            edhrec_decks=row.num_decks,
            type_line=row.type_line,
            cmc=cmc,
            role_quality_by_role=rq_by_role,
            oracle_text=oracle_text,
            price_usd=price,
            salt_score=salt,
            commander_inclusion=cmd_inclusion,
            commander_synergy=cmd_synergy,
        )

        # Tier assignment
        if in_combo:
            card.tier = "CORE"
        elif (row.arch_score or 0) >= 4 and (row.brack_score or 0) >= 3:
            card.tier = "CORE"
        elif (row.arch_score or 0) >= 3 or rq >= 2.5:
            card.tier = "SUPPORT"
        else:
            card.tier = "FLEX"

        all_scored.append(card)

    # --- Known lands enter the pool even without classification ---
    # Most nonbasic lands are never LLM-classified, but lands.py knows their
    # quality tiers. Without this, mana bases collapse to basics plus whatever
    # lands happen to be classified — and EDHREC overlap craters.
    from mtg_evaluator.deckbuilding.lands import LAND_SCORES

    seen_oracle_ids = {c.oracle_id for c in all_scored}
    extra_land_rows = session.execute(
        select(
            Card.oracle_id,
            Card.name,
            Card.type_line,
            Card.color_identity,
            Card.cmc,
            Card.oracle_text,
            Card.price_usd,
            EDHRecCardStats.num_decks,
            EDHRecCardStats.salt_score,
        )
        .join(
            EDHRecCardStats, EDHRecCardStats.oracle_id == Card.oracle_id, isouter=True
        )
        .where(Card.is_land == True)  # noqa: E712
        .where(Card.name.in_(list(LAND_SCORES.keys())))
        .where(Card.oracle_id != commander.oracle_id)
    ).all()

    for row in extra_land_rows:
        if row.oracle_id in seen_oracle_ids:
            continue
        card_colors = set(row.color_identity or [])
        if card_colors and not card_colors.issubset(deck_colors_set):
            continue
        is_gc = row.name in GAME_CHANGERS
        if is_gc and request.bracket < 3:
            continue
        price = float(row.price_usd) if row.price_usd is not None else None
        if (
            request.max_card_price is not None
            and price is not None
            and price > request.max_card_price
        ):
            continue
        salt = float(row.salt_score) if row.salt_score is not None else None
        if salt_cap is not None and salt is not None and salt > salt_cap:
            continue
        ls = land_score(row.name, request.bracket)
        if ls is None:
            continue  # bracket-restricted land (e.g. Ancient Tomb below B3)
        cmd_stat = cmd_stats.get(row.name)
        seen_oracle_ids.add(row.oracle_id)
        all_scored.append(
            PoolCard(
                name=row.name,
                oracle_id=row.oracle_id,
                tier="SUPPORT",
                score=ls,
                archetype_score=None,
                bracket_score=None,
                role_quality=0.0,
                functions=[],
                is_game_changer=is_gc,
                combo_ids=[],
                edhrec_decks=row.num_decks,
                type_line=row.type_line,
                cmc=float(row.cmc or 0),
                oracle_text=row.oracle_text or "",
                price_usd=price,
                salt_score=salt,
                commander_inclusion=(
                    cmd_stat["inclusion_rate"] if cmd_stat else None
                ),
                commander_synergy=cmd_stat.get("synergy") if cmd_stat else None,
            )
        )

    # Sort and assign to tiers
    all_scored.sort(key=lambda c: c.score, reverse=True)
    for card in all_scored:
        if card.tier == "CORE":
            pool.core.append(card)
        elif card.tier == "SUPPORT":
            pool.support.append(card)
        else:
            pool.flex.append(card)

    # Compute avg CMC for role target calculation (non-land cards in CORE)
    nonland_core = [c for c in pool.core if "land" not in c.type_line.lower()]
    avg_cmc = (
        (sum(c.cmc for c in nonland_core) / len(nonland_core)) if nonland_core else 3.5
    )

    # Dynamic role targets (Phase 5)
    role_targets = compute_role_targets(
        bracket=request.bracket,
        profile=profile,
        archetype=canonical_archetype,
        avg_cmc=avg_cmc,
    )
    pool.role_targets = role_targets

    # Apply saturation multiplier to SUPPORT + FLEX cards
    # (down-weight role cards when we already have enough in CORE)
    role_counts_in_core: dict[str, int] = {}
    for card in pool.core:
        for fn in card.functions:
            bucket = role_bucket(fn)
            if bucket in _NEEDED_ROLES:
                role_counts_in_core[bucket] = role_counts_in_core.get(bucket, 0) + 1

    for card in pool.support + pool.flex:
        # At optimized style, consensus staples are exempt from saturation —
        # if 40%+ of this commander's decks play it, it has earned a slot
        # regardless of how many same-role cards the CORE tier already has.
        if (
            request.build_style == "optimized"
            and (card.commander_inclusion or 0) >= 0.4
        ):
            continue
        role_scores = {
            role: quality
            for role, quality in card.role_quality_by_role.items()
            if role in _NEEDED_ROLES
        }
        if role_scores:
            role_needs = {
                role: max(
                    0, role_targets.get(role, 8) - role_counts_in_core.get(role, 0)
                )
                for role in role_scores
            }
            previous_rq = card.role_quality
            card.role_quality = need_weighted_role_quality(role_scores, role_needs)
            if previous_rq != card.role_quality:
                # Replace the 25-point role component without recomputing the
                # whole composite score.
                card.score = round(
                    min(
                        100.0,
                        max(0.0, card.score + (card.role_quality - previous_rq) * 5),
                    ),
                    2,
                )

            best_role = max(
                role_scores,
                key=lambda role: (
                    role_needs.get(role, 0),
                    role_scores.get(role, 0.0),
                ),
                default=None,
            )
            if best_role:
                target = role_targets.get(best_role, 8)
                current = role_counts_in_core.get(best_role, 0)
                sat = saturation_multiplier(best_role, current, target)
                if sat != 1.0:
                    card.score = round(card.score * sat, 2)

    # Re-sort after saturation adjustment
    pool.support.sort(key=lambda c: c.score, reverse=True)
    pool.flex.sort(key=lambda c: c.score, reverse=True)

    # Trim to pool_size. Lands are capped separately — the full land tier list
    # (80+ playable lands) must never crowd spell candidates out of the pool.
    def _is_land(c: PoolCard) -> bool:
        return "land" in c.type_line.lower()

    land_target = role_targets.lands if role_targets else 36
    land_keep = min(land_target + 12, 48, max(10, request.pool_size // 4))
    kept_lands = sorted(
        (c for c in pool.core + pool.support + pool.flex if _is_land(c)),
        key=lambda c: c.score,
        reverse=True,
    )[:land_keep]

    core_nl = [c for c in pool.core if not _is_land(c)]
    support_nl = [c for c in pool.support if not _is_land(c)]
    flex_nl = [c for c in pool.flex if not _is_land(c)]

    nonland_target = max(0, request.pool_size - len(kept_lands))
    core_cap = min(len(core_nl), nonland_target // 4)
    support_cap = min(len(support_nl), nonland_target // 2)
    flex_cap = nonland_target - core_cap - support_cap

    pool.core = core_nl[:core_cap]
    pool.support = support_nl[:support_cap] + kept_lands
    pool.flex = flex_nl[:flex_cap]

    # Consensus floor (optimized style): cards in 40%+ of this commander's
    # decks always survive the trim. The skeleton still picks by score — if
    # a consensus card loses its slot, the EDHREC comparison reports exactly
    # what outscored it, which is the defensible difference we want.
    if request.build_style == "optimized":
        kept_names = {c.name for c in pool.all_cards}
        consensus_dropped = [
            c
            for c in core_nl[core_cap:] + support_nl[support_cap:] + flex_nl[flex_cap:]
            if (c.commander_inclusion or 0) >= 0.4 and c.name not in kept_names
        ]
        pool.flex.extend(consensus_dropped[:30])

    # Sort combos by bracket tag priority
    tag_order = {"R": 0, "S": 1, "P": 2, "O": 3, "C": 4, "E": 5}
    pool.combos.sort(key=lambda c: (tag_order.get(c.bracket_tag, 9), len(c.card_names)))

    # --- Package health check ---
    all_pool_functions: list[str] = []
    for card in pool.all_cards:
        all_pool_functions.extend(card.functions)

    pool.package_health = check_package_health(canonical_archetype, all_pool_functions)

    # --- Nonbo detection ---
    all_card_functions_map = {c.name: c.functions for c in pool.all_cards}
    all_card_names = [c.name for c in pool.all_cards]
    ramp_count = sum(1 for c in pool.all_cards if has_function(c.functions, "ramp"))
    board_wipe_count = sum(
        1 for c in pool.all_cards if has_function(c.functions, "board_wipe")
    )
    creature_count = sum(1 for c in pool.all_cards if "creature" in c.type_line.lower())

    pool.nonbo_warnings = detect_nonbos(
        archetype=canonical_archetype,
        all_functions=all_pool_functions,
        all_card_names=all_card_names,
        all_card_functions=all_card_functions_map,
        ramp_count=ramp_count,
        avg_cmc=avg_cmc,
        land_count=sum(1 for c in pool.all_cards if "land" in c.type_line.lower()),
        board_wipe_count=board_wipe_count,
        creature_count=creature_count,
    )

    # --- Consistency math ---
    land_count_in_pool = sum(1 for c in pool.all_cards if "land" in c.type_line.lower())
    interaction_count = sum(
        1
        for c in pool.all_cards
        if any(role_bucket(f) in _INTERACTION_ROLES for f in c.functions)
    )
    enabler_count = 0
    payoff_count = 0
    if canonical_archetype:
        from mtg_evaluator.deckbuilding.packages import ARCHETYPE_PACKAGES

        pkg = ARCHETYPE_PACKAGES.get(canonical_archetype)
        if pkg:
            enabler_count = sum(
                1
                for c in pool.all_cards
                if any(
                    f in pkg.enabler_functions for f in normalize_functions(c.functions)
                )
            )
            payoff_count = sum(
                1
                for c in pool.all_cards
                if any(
                    f in pkg.payoff_functions for f in normalize_functions(c.functions)
                )
            )

    pool.consistency = compute_consistency(
        land_count=land_count_in_pool,
        ramp_count=ramp_count,
        interaction_count=interaction_count,
        commander_cmc=float(commander.cmc or 3),
        enabler_count=enabler_count,
        payoff_count=payoff_count,
        deck_size=98 if partner_card else 99,
    )

    return pool
