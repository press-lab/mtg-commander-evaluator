"""
Card pool builder — given a DeckRequest, queries the DB and returns a ranked
list of candidate cards, grouped by tier.

Scoring (0–100 composite):
  archetype_fit    (0-5 from LLM classification) → weight 40
  bracket_fit      (0-5 bracket score for requested level) → weight 20
  role_value       (does it fill a needed role?) → weight 20
  combo_membership (part of a legal combo for this deck?) → weight 10
  popularity       (EDHREC num_decks, log-normalized) → weight 10

Cards are returned in four tiers:
  CORE        — archetype score ≥ 4, bracket-legal, role-filling
  COMBO       — combo pieces (all cards from legal combos available in color)
  SUPPORT     — archetype score 3-4 or fills a needed role
  FLEX        — everything else that passes color + bracket filter
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from mtg_evaluator.db.models import (
    Card, CardClassification, CardArchetypeScore, CardBracketScore,
    CardFunction, EDHRecCardStats, SpellbookCombo, SpellbookComboCard,
)
from mtg_evaluator.evaluation.evaluator import GAME_CHANGERS
from mtg_evaluator.deckbuilding.request import DeckRequest
from mtg_evaluator.deckbuilding.archetypes import normalize_archetype
from mtg_evaluator.deckbuilding.lands import land_score

Tier = Literal["CORE", "COMBO", "SUPPORT", "FLEX"]

_BRACKET_KEY = {1: "casual", 2: "bracket_2", 3: "bracket_3", 4: "bracket_4", 5: "cedh"}


@dataclass
class PoolCard:
    name: str
    oracle_id: str
    tier: Tier
    score: float                    # 0-100 composite
    archetype_score: float | None   # 0-5
    bracket_score: float | None     # 0-5 at requested bracket
    functions: list[str]
    is_game_changer: bool
    combo_ids: list[str]            # spellbook combo IDs this card enables
    edhrec_decks: int | None
    type_line: str


@dataclass
class ComboSummary:
    spellbook_id: str
    bracket_tag: str
    card_names: list[str]
    results_description: str | None


@dataclass
class CardPool:
    request: DeckRequest
    commander_name: str
    color_identity: list[str]
    archetype: str | None

    core: list[PoolCard] = field(default_factory=list)
    combos: list[ComboSummary] = field(default_factory=list)
    support: list[PoolCard] = field(default_factory=list)
    flex: list[PoolCard] = field(default_factory=list)

    @property
    def all_cards(self) -> list[PoolCard]:
        return self.core + self.support + self.flex

    @property
    def total(self) -> int:
        return len(self.all_cards)


def _log_popularity(num_decks: int | None) -> float:
    if not num_decks or num_decks <= 0:
        return 0.0
    return min(math.log10(num_decks) / 5.0, 1.0)  # log10(100k) / 5 = 1.0


def _composite_score(
    archetype_score: float | None,
    bracket_score: float | None,
    fills_role: bool,
    in_combo: bool,
    popularity: float,
) -> float:
    a = (archetype_score or 0) / 5.0     # 0-1
    b = (bracket_score or 0) / 5.0       # 0-1
    r = 1.0 if fills_role else 0.0
    c = 1.0 if in_combo else 0.0
    p = popularity                        # 0-1

    return round((a * 40 + b * 20 + r * 20 + c * 10 + p * 10), 2)


def build_card_pool(session: Session, request: DeckRequest) -> CardPool:
    """
    Main entry point. Resolves commander, fetches candidate cards, scores and tiers them.
    """
    # --- Resolve commander ---
    commander = session.scalars(
        select(Card).where(Card.name == request.commander_name)
    ).first()
    if not commander:
        raise ValueError(f"Commander '{request.commander_name}' not found in database.")

    # --- Resolve partner (if any) and merge color identity ---
    partner_card: Card | None = None
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

    # Normalize archetype: "sac" → "aristocrats", "wheels" → "spellslinger", etc.
    canonical_archetype = normalize_archetype(request.archetype)
    bracket_key = _BRACKET_KEY.get(request.bracket, "bracket_3")

    partner_display = (
        f"{commander.name} + {partner_card.name}" if partner_card else commander.name
    )

    pool = CardPool(
        request=request,
        commander_name=partner_display,
        color_identity=color_identity,
        archetype=canonical_archetype,
    )

    # --- Find legal combos in color identity ---
    legal_combo_ids: set[str] = set()
    combo_oracle_sets: dict[str, set[str]] = {}   # combo_id → set of oracle_ids

    if request.want_combos and request.allowed_combo_tags:
        combo_rows = session.execute(
            select(SpellbookCombo.spellbook_id, SpellbookCombo.bracket_tag,
                   SpellbookCombo.results_description)
            .where(SpellbookCombo.bracket_tag.in_(request.allowed_combo_tags))
        ).all()

        for combo_id, btag, desc in combo_rows:
            card_rows = session.execute(
                select(SpellbookComboCard.oracle_id, SpellbookComboCard.card_name)
                .where(SpellbookComboCard.combo_id == combo_id)
                .where(SpellbookComboCard.oracle_id.isnot(None))
            ).all()

            if not card_rows:
                continue

            # Check color identity — all combo cards must fit in commander's colors
            oracle_ids = [r.oracle_id for r in card_rows]
            card_names_map = {r.oracle_id: r.card_name for r in card_rows}

            combo_cards = session.execute(
                select(Card.oracle_id, Card.color_identity)
                .where(Card.oracle_id.in_(oracle_ids))
            ).all()

            deck_colors = set(color_identity) | {"C"}
            fits = all(
                set(c.color_identity or []).issubset(deck_colors)
                for c in combo_cards
            )
            if not fits:
                continue

            legal_combo_ids.add(combo_id)
            combo_oracle_sets[combo_id] = set(oracle_ids)

            pool.combos.append(ComboSummary(
                spellbook_id=combo_id,
                bracket_tag=btag or "",
                card_names=[card_names_map[oid] for oid in oracle_ids],
                results_description=desc,
            ))

    combo_oracle_union: set[str] = set().union(*combo_oracle_sets.values()) if combo_oracle_sets else set()

    # --- Load all classified cards in color identity ---
    # Get latest valid classification per card
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
            CardArchetypeScore.score.label("arch_score"),
            CardBracketScore.score.label("brack_score"),
            EDHRecCardStats.num_decks,
        )
        .join(cls_subq, cls_subq.c.oracle_id == Card.oracle_id)
        .join(CardArchetypeScore,
              (CardArchetypeScore.classification_id == cls_subq.c.id) &
              (CardArchetypeScore.archetype == (canonical_archetype or "")),
              isouter=True)
        .join(CardBracketScore,
              (CardBracketScore.classification_id == cls_subq.c.id) &
              (CardBracketScore.bracket_level == bracket_key),
              isouter=True)
        .join(EDHRecCardStats, EDHRecCardStats.oracle_id == Card.oracle_id, isouter=True)
        .where(Card.oracle_id != commander.oracle_id)
        .where(
            Card.oracle_id != partner_card.oracle_id
            if partner_card else True
        )
    ).all()

    # Build function map (classification_id → functions) in one query
    cls_ids_by_oracle: dict[str, str] = {}
    for row in session.execute(select(cls_subq)).all():
        cls_ids_by_oracle[row.oracle_id] = row.id

    fn_map: dict[str, list[str]] = {}
    if cls_ids_by_oracle:
        fn_rows = session.execute(
            select(CardFunction.classification_id, CardFunction.function_name)
            .where(CardFunction.classification_id.in_(cls_ids_by_oracle.values()))
        ).all()
        cls_to_oracle = {v: k for k, v in cls_ids_by_oracle.items()}
        for fn_row in fn_rows:
            oid = cls_to_oracle.get(fn_row.classification_id)
            if oid:
                fn_map.setdefault(oid, []).append(fn_row.function_name)

    # Tutors are a needed role at every bracket — land tutors at B1, efficient
    # tutors at B4. GC tutors are already bracket-filtered before this point.
    needed_roles = {"ramp", "draw", "removal", "board_wipe", "protection", "tutor"}
    deck_colors_set = set(color_identity) | {"C"}

    for row in rows:
        # Color identity filter
        card_colors = set(row.color_identity or [])
        if card_colors and not card_colors.issubset(deck_colors_set):
            continue

        # Game changer filter: only include GCs if bracket allows (≥3 includes up to 3)
        is_gc = row.name in GAME_CHANGERS
        if is_gc and request.bracket < 3:
            continue   # B1/B2 allow 0 GCs

        functions = fn_map.get(row.oracle_id, [])
        fills_role = bool(needed_roles.intersection(functions))
        in_combo = row.oracle_id in combo_oracle_union
        popularity = _log_popularity(row.num_decks)
        # Tutor density affects score, not legality.
        # GC tutors (Demonic Tutor, Vampiric Tutor, etc.) are already handled
        # by the Game Changer bracket filter above — they're legal at B3+.
        # Non-GC tutors (Cultivate, Farseek, Sylvan Scrying, etc.) are always
        # legal at every bracket. Density = how much weight to give tutors.
        is_tutor = "tutor" in functions
        tutor_multiplier = {
            "none":  0.5,   # de-emphasize tutors but don't exclude
            "light": 1.0,   # normal weight
            "heavy": 1.3,   # boost tutors up the list
        }.get(request.tutor_density, 1.0)

        # Lands: use quality tier score instead of archetype-fit composite.
        # A Polluted Delta's "archetype score" is meaningless; its land quality isn't.
        is_land = "land" in row.type_line.lower()
        if is_land:
            ls = land_score(row.name, request.bracket)
            score = ls if ls is not None else _composite_score(
                archetype_score=row.arch_score,
                bracket_score=row.brack_score,
                fills_role=fills_role,
                in_combo=in_combo,
                popularity=popularity,
            )
        else:
            score = _composite_score(
                archetype_score=row.arch_score,
                bracket_score=row.brack_score,
                fills_role=fills_role,
                in_combo=in_combo,
                popularity=popularity,
            )
            if is_tutor:
                score = round(score * tutor_multiplier, 2)

        combo_ids_for_card = [
            cid for cid, oracles in combo_oracle_sets.items()
            if row.oracle_id in oracles
        ]

        card = PoolCard(
            name=row.name,
            oracle_id=row.oracle_id,
            tier="FLEX",  # assigned below
            score=score,
            archetype_score=row.arch_score,
            bracket_score=row.brack_score,
            functions=functions,
            is_game_changer=is_gc,
            combo_ids=combo_ids_for_card,
            edhrec_decks=row.num_decks,
            type_line=row.type_line,
        )

        # Tier assignment
        if in_combo:
            card.tier = "CORE"
        elif (row.arch_score or 0) >= 4 and (row.brack_score or 0) >= 3:
            card.tier = "CORE"
        elif (row.arch_score or 0) >= 3 or fills_role:
            card.tier = "SUPPORT"
        else:
            card.tier = "FLEX"

        if card.tier == "CORE":
            pool.core.append(card)
        elif card.tier == "SUPPORT":
            pool.support.append(card)
        else:
            pool.flex.append(card)

    # Sort each tier by score descending
    pool.core.sort(key=lambda c: c.score, reverse=True)
    pool.support.sort(key=lambda c: c.score, reverse=True)
    pool.flex.sort(key=lambda c: c.score, reverse=True)

    # Trim to pool_size
    total_target = request.pool_size
    core_cap = min(len(pool.core), total_target // 4)
    support_cap = min(len(pool.support), total_target // 2)
    flex_cap = total_target - core_cap - support_cap

    pool.core = pool.core[:core_cap]
    pool.support = pool.support[:support_cap]
    pool.flex = pool.flex[:flex_cap]

    # Sort combos by bracket tag priority
    tag_order = {"R": 0, "S": 1, "P": 2, "O": 3, "C": 4, "E": 5}
    pool.combos.sort(key=lambda c: (tag_order.get(c.bracket_tag, 9), len(c.card_names)))

    return pool
