"""
Recommendation engine for the deck evaluator.

Produces three kinds of actionable suggestions for an uploaded decklist:

1. Missing staples — cards with high inclusion/synergy for THIS commander
   (per-commander EDHREC data) that the deck doesn't run. This is the classic
   "78% of Henzie decks play this" recommendation.
2. Gap fillers — for each role below target (ramp, draw, removal, ...), the
   best classified candidates ranked by role quality, archetype fit, and
   commander synergy.
3. Cut candidates — cards already in the deck that score poorly on every
   signal (no role, weak archetype fit, negative commander synergy), worst
   first. Conservative: a card must fail multiple signals to be flagged.

All suggestions respect color identity and bracket rules (no Game Changers
below B3, no mass land denial below B4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from mtg_evaluator.card_functions import (
    function_group,
    normalize_functions,
    role_bucket,
)
from mtg_evaluator.db.models import (
    Card,
    CardArchetypeScore,
    CardClassification,
    CardFunction,
    EDHRecCardStats,
)
from mtg_evaluator.evaluation.game_changers import is_game_changer
from mtg_evaluator.evaluation.parser import ParsedCard
from mtg_evaluator.deckbuilding.role_quality import compute_role_quality

# Roles we actively recommend fillers for (lands handled by mana analysis)
_FILLABLE_ROLES = {"ramp", "draw", "removal", "board_wipe", "protection", "finisher"}

# Minimum per-commander inclusion for a "missing staple" suggestion
_STAPLE_INCLUSION_FLOOR = 0.30
# Or a strong synergy signal even at lower inclusion
_STAPLE_SYNERGY_FLOOR = 0.20


@dataclass
class Recommendation:
    name: str
    oracle_id: str | None
    reason: str
    score: float  # internal ranking score
    role: str | None = None
    inclusion_rate: float | None = None
    synergy: float | None = None
    archetype_score: float | None = None
    role_quality: float | None = None
    price_usd: float | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "oracle_id": self.oracle_id,
            "reason": self.reason,
            "role": self.role,
            "inclusion_rate": self.inclusion_rate,
            "synergy": self.synergy,
            "archetype_score": self.archetype_score,
            "role_quality": self.role_quality,
            "price_usd": self.price_usd,
        }


@dataclass
class RecommendationReport:
    staples_missing: list[Recommendation] = field(default_factory=list)
    gap_fillers: dict[str, list[Recommendation]] = field(default_factory=dict)
    cut_candidates: list[Recommendation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "staples_missing": [r.to_dict() for r in self.staples_missing],
            "gap_fillers": {
                role: [r.to_dict() for r in recs]
                for role, recs in self.gap_fillers.items()
            },
            "cut_candidates": [r.to_dict() for r in self.cut_candidates],
        }


def _bracket_legal(name: str, bracket_int: int, mass_land_denial: frozenset[str]) -> bool:
    if bracket_int < 3 and is_game_changer(name):
        return False
    if bracket_int < 4 and name in mass_land_denial:
        return False
    return True


def _fits_colors(card_colors: list | None, deck_colors: set[str]) -> bool:
    colors = set(card_colors or [])
    return not colors or colors.issubset(deck_colors | {"C"})


# ---------------------------------------------------------------------------
# 1. Missing staples (per-commander EDHREC)
# ---------------------------------------------------------------------------

def find_missing_staples(
    session: Session,
    cmd_stats: dict[str, dict],
    deck_card_names: set[str],
    deck_colors: set[str],
    bracket_int: int,
    mass_land_denial: frozenset[str],
    limit: int = 10,
) -> list[Recommendation]:
    """Cards heavily played with this commander that the deck is missing."""
    if not cmd_stats:
        return []

    deck_front_names = {n.split(" // ")[0] for n in deck_card_names}
    candidates = [
        (name, stat)
        for name, stat in cmd_stats.items()
        if name not in deck_card_names
        and name.split(" // ")[0] not in deck_front_names
        and (
            (stat.get("inclusion_rate") or 0) >= _STAPLE_INCLUSION_FLOOR
            or (stat.get("synergy") or 0) >= _STAPLE_SYNERGY_FLOOR
        )
        and _bracket_legal(name, bracket_int, mass_land_denial)
    ]
    if not candidates:
        return []

    # Resolve color identity + price for the candidates in one query
    names = [name for name, _ in candidates]
    rows = session.execute(
        select(Card.name, Card.oracle_id, Card.color_identity, Card.price_usd)
        .where(Card.name.in_(names))
    ).all()
    card_info = {r.name: r for r in rows}

    recs: list[Recommendation] = []
    for name, stat in candidates:
        info = card_info.get(name) or card_info.get(name.split(" // ")[0])
        if info is None or not _fits_colors(info.color_identity, deck_colors):
            continue
        inclusion = stat.get("inclusion_rate") or 0.0
        synergy = stat.get("synergy")
        rank_score = inclusion * 60 + max(0.0, synergy or 0.0) * 40
        reason = f"In {inclusion:.0%} of decks for this commander"
        if synergy is not None and synergy >= 0.15:
            reason += f" (synergy +{synergy:.2f})"
        recs.append(
            Recommendation(
                name=name,
                oracle_id=info.oracle_id,
                reason=reason,
                score=round(rank_score, 2),
                inclusion_rate=inclusion,
                synergy=synergy,
                price_usd=float(info.price_usd) if info.price_usd is not None else None,
            )
        )

    recs.sort(key=lambda r: r.score, reverse=True)
    return recs[:limit]


# ---------------------------------------------------------------------------
# 2. Gap fillers
# ---------------------------------------------------------------------------

def find_gap_fillers(
    session: Session,
    role_gaps: dict[str, int],  # role -> cards short of target
    archetype: str | None,
    cmd_stats: dict[str, dict],
    deck_oracle_ids: set[str],
    deck_colors: set[str],
    bracket_int: int,
    mass_land_denial: frozenset[str],
    per_role_limit: int = 5,
) -> dict[str, list[Recommendation]]:
    """Best classified candidates for each under-target role."""
    out: dict[str, list[Recommendation]] = {}
    roles = [r for r, gap in role_gaps.items() if gap > 0 and r in _FILLABLE_ROLES]
    if not roles:
        return out

    cls_subq = (
        select(CardClassification.id, CardClassification.oracle_id)
        .where(CardClassification.is_valid == True)  # noqa: E712
        .order_by(CardClassification.oracle_id, CardClassification.classified_at.desc())
        .distinct(CardClassification.oracle_id)
        .subquery()
    )

    for role in roles:
        # All raw function spellings that collapse into this role bucket
        fn_names = set(function_group(role))
        fn_names |= {
            fn for fn in (
                "creature_removal", "artifact_removal", "enchantment_removal",
            ) if role == "removal"
        }
        if role == "ramp":
            fn_names |= {"land_ramp", "treasure_maker", "mana_rock", "mana_dork", "mana_ramp"}
        if role == "draw":
            fn_names |= {"card_draw", "card_advantage", "impulse_draw"}

        rows = session.execute(
            select(
                Card.oracle_id,
                Card.name,
                Card.color_identity,
                Card.cmc,
                Card.is_instant,
                Card.is_sorcery,
                Card.oracle_text,
                Card.price_usd,
                CardArchetypeScore.score.label("arch_score"),
                EDHRecCardStats.num_decks,
            )
            .join(cls_subq, cls_subq.c.oracle_id == Card.oracle_id)
            .join(
                CardFunction,
                (CardFunction.classification_id == cls_subq.c.id)
                & (CardFunction.function_name.in_(fn_names)),
            )
            .join(
                CardArchetypeScore,
                (CardArchetypeScore.classification_id == cls_subq.c.id)
                & (CardArchetypeScore.archetype == (archetype or "")),
                isouter=True,
            )
            .join(
                EDHRecCardStats,
                EDHRecCardStats.oracle_id == Card.oracle_id,
                isouter=True,
            )
            .where(Card.oracle_id.notin_(deck_oracle_ids))
            .where(Card.is_land == False)  # noqa: E712
            .distinct(Card.oracle_id)
        ).all()

        scored: list[Recommendation] = []
        for row in rows:
            if not _fits_colors(row.color_identity, deck_colors):
                continue
            if not _bracket_legal(row.name, bracket_int, mass_land_denial):
                continue

            quality = compute_role_quality(
                role=role,
                cmc=float(row.cmc or 0),
                is_instant=bool(row.is_instant),
                is_sorcery=bool(row.is_sorcery),
                functions=[role],
                is_game_changer=is_game_changer(row.name),
                oracle_text=row.oracle_text or "",
            )
            stat = cmd_stats.get(row.name) or cmd_stats.get(row.name.split(" // ")[0])
            inclusion = stat.get("inclusion_rate") if stat else None
            synergy = stat.get("synergy") if stat else None

            rank = (
                quality / 5.0 * 40
                + (row.arch_score or 0) / 5.0 * 20
                + (inclusion or 0.0) * 25
                + max(0.0, synergy or 0.0) * 15
            )

            reason_bits = [f"{role.replace('_', ' ')} quality {quality:.1f}/5"]
            if inclusion:
                reason_bits.append(f"in {inclusion:.0%} of this commander's decks")
            elif row.arch_score:
                reason_bits.append(f"archetype fit {row.arch_score}/5")
            scored.append(
                Recommendation(
                    name=row.name,
                    oracle_id=row.oracle_id,
                    reason=", ".join(reason_bits),
                    score=round(rank, 2),
                    role=role,
                    inclusion_rate=inclusion,
                    synergy=synergy,
                    archetype_score=row.arch_score,
                    role_quality=round(quality, 2),
                    price_usd=float(row.price_usd) if row.price_usd is not None else None,
                )
            )

        scored.sort(key=lambda r: r.score, reverse=True)
        if scored:
            out[role] = scored[:per_role_limit]

    return out


# ---------------------------------------------------------------------------
# 3. Cut candidates
# ---------------------------------------------------------------------------

def find_cut_candidates(
    parsed_cards: list[ParsedCard],
    classifications: dict[str, dict],
    cards_by_oracle: dict[str, Card],
    archetype: str | None,
    cmd_stats: dict[str, dict],
    combo_oracle_ids: set[str],
    limit: int = 6,
) -> list[Recommendation]:
    """
    Weakest cards in the deck. Conservative: a card is only flagged when it
    fails several independent signals. Lands, the commander, combo pieces,
    and unclassified cards are never flagged.
    """
    recs: list[Recommendation] = []

    for pc in parsed_cards:
        if pc.is_commander or pc.oracle_id in combo_oracle_ids:
            continue
        card = cards_by_oracle.get(pc.oracle_id)
        if card is None or card.is_land:
            continue
        cls = classifications.get(pc.oracle_id) or {}
        functions = normalize_functions(cls.get("functions", []))
        if not cls.get("functions") and not cls.get("archetype_scores"):
            continue  # unclassified — can't judge fairly

        arch_score = (cls.get("archetype_scores") or {}).get(archetype) if archetype else None
        stat = cmd_stats.get(card.name) or cmd_stats.get(card.name.split(" // ")[0])
        synergy = stat.get("synergy") if stat else None
        inclusion = stat.get("inclusion_rate") if stat else None
        has_role = any(role_bucket(fn) in _FILLABLE_ROLES | {"tutor"} for fn in functions)

        strikes: list[str] = []
        if archetype and (arch_score or 0) <= 2:
            strikes.append(f"weak {archetype} fit ({arch_score or 0}/5)")
        if not has_role:
            strikes.append("fills no core role")
        if synergy is not None and synergy < 0:
            strikes.append(f"negative synergy with commander ({synergy:+.2f})")
        if inclusion is not None and inclusion < 0.05:
            strikes.append(f"rarely played here ({inclusion:.0%} of decks)")

        if len(strikes) < 2:
            continue

        badness = len(strikes) * 10 - (arch_score or 0) * 2 + (
            abs(min(0.0, synergy or 0.0)) * 20
        )
        recs.append(
            Recommendation(
                name=card.name,
                oracle_id=card.oracle_id,
                reason="; ".join(strikes),
                score=round(badness, 2),
                archetype_score=arch_score,
                synergy=synergy,
                inclusion_rate=inclusion,
            )
        )

    recs.sort(key=lambda r: r.score, reverse=True)
    return recs[:limit]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_recommendations(
    session: Session,
    parsed_cards: list[ParsedCard],
    classifications: dict[str, dict],
    cards_by_oracle: dict[str, Card],
    commander: Card | None,
    partner: Card | None,
    archetype: str | None,
    bracket_int: int,
    role_gaps: dict[str, int],
    combo_oracle_ids: set[str],
    mass_land_denial: frozenset[str],
) -> RecommendationReport:
    """Assemble the full recommendation report for an evaluated deck."""
    deck_colors = set(commander.color_identity or []) if commander else set()
    if partner:
        deck_colors |= set(partner.color_identity or [])
    deck_oracle_ids = {pc.oracle_id for pc in parsed_cards}
    deck_card_names = {
        cards_by_oracle[pc.oracle_id].name
        for pc in parsed_cards
        if pc.oracle_id in cards_by_oracle
    }

    cmd_stats: dict[str, dict] = {}
    if commander is not None:
        from mtg_evaluator.ingestion.edhrec_commander import (
            get_or_fetch_commander_stats,
        )

        try:
            cmd_stats = get_or_fetch_commander_stats(session, commander, partner)
        except Exception:
            cmd_stats = {}

    return RecommendationReport(
        staples_missing=find_missing_staples(
            session,
            cmd_stats,
            deck_card_names,
            deck_colors,
            bracket_int,
            mass_land_denial,
        ),
        gap_fillers=find_gap_fillers(
            session,
            role_gaps,
            archetype,
            cmd_stats,
            deck_oracle_ids,
            deck_colors,
            bracket_int,
            mass_land_denial,
        ),
        cut_candidates=find_cut_candidates(
            parsed_cards,
            classifications,
            cards_by_oracle,
            archetype,
            cmd_stats,
            combo_oracle_ids,
        ),
    )
