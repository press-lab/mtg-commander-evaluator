"""
Card browser — no commander needed.

"What are the best ramp cards for a B3 sacrifice deck?"
"Show me the most-played draw spells at B4."
"What removal works in a B2 tokens deck?"

Returns a ranked flat list of cards matching the filters,
ordered by composite score (archetype fit + bracket fit + EDHREC popularity).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from mtg_evaluator.card_functions import (
    has_function,
    normalize_function,
    normalize_functions,
)
from mtg_evaluator.db.models import (
    Card,
    CardClassification,
    CardArchetypeScore,
    CardBracketScore,
    CardFunction,
    EDHRecCardStats,
)
from mtg_evaluator.evaluation.evaluator import GAME_CHANGERS
from mtg_evaluator.deckbuilding.pool import _log_popularity

_BRACKET_KEY = {1: "casual", 2: "bracket_2", 3: "bracket_3", 4: "bracket_4", 5: "cedh"}


@dataclass
class BrowseCard:
    name: str
    oracle_id: str
    type_line: str
    archetype_score: float | None
    bracket_score: float | None
    functions: list[str]
    is_game_changer: bool
    edhrec_decks: int | None
    score: float


def browse_cards(
    session: Session,
    archetype: str | None = None,
    bracket: int = 3,
    role: str | None = None,  # "ramp", "draw", "removal", "tutor", etc.
    colors: list[str] | None = None,
    limit: int = 100,
    min_archetype_score: float = 2.0,
) -> list[BrowseCard]:
    """
    Return cards ranked by fit for the given archetype/bracket/role.
    Commander-agnostic — useful for exploring card space before picking a commander.

    Tutors are included at every bracket. GC tutors show up correctly
    at B3+ since they're valid there.
    """
    bracket_key = _BRACKET_KEY.get(bracket, "bracket_3")
    role = normalize_function(role) if role else None

    cls_subq = (
        select(CardClassification.id, CardClassification.oracle_id)
        .where(CardClassification.is_valid == True)  # noqa: E712
        .order_by(CardClassification.oracle_id, CardClassification.classified_at.desc())
        .distinct(CardClassification.oracle_id)
        .subquery()
    )

    q = (
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
        .join(
            CardArchetypeScore,
            (CardArchetypeScore.classification_id == cls_subq.c.id)
            & (CardArchetypeScore.archetype == (archetype or "")),
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
    )

    if archetype:
        q = q.where(CardArchetypeScore.score >= min_archetype_score)

    rows = session.execute(q.limit(limit * 5)).all()

    # Build function map
    cls_ids = session.execute(select(cls_subq)).all()
    oracle_to_cls = {r.oracle_id: r.id for r in cls_ids}
    cls_to_oracle = {v: k for k, v in oracle_to_cls.items()}

    fn_map: dict[str, list[str]] = {}
    if oracle_to_cls:
        fn_rows = session.execute(
            select(CardFunction.classification_id, CardFunction.function_name).where(
                CardFunction.classification_id.in_(oracle_to_cls.values())
            )
        ).all()
        for fn_row in fn_rows:
            oid = cls_to_oracle.get(fn_row.classification_id)
            if oid:
                fn_map.setdefault(oid, []).append(fn_row.function_name)

    color_filter = set(colors) if colors else None
    results: list[BrowseCard] = []

    for row in rows:
        # Color filter
        if color_filter:
            card_colors = set(row.color_identity or [])
            if card_colors and not card_colors.issubset(color_filter | {"C"}):
                continue

        # GC filter: B1/B2 decks shouldn't see GC cards in browser
        is_gc = row.name in GAME_CHANGERS
        if is_gc and bracket < 3:
            continue

        functions = normalize_functions(fn_map.get(row.oracle_id, []))

        # Role filter — tutors always pass regardless of role filter
        is_tutor = has_function(functions, "tutor")
        if role and not is_tutor:
            if not has_function(functions, role):
                continue

        popularity = _log_popularity(row.num_decks)
        arch = row.arch_score or 0
        brack = row.brack_score or 0
        score = round(
            (arch / 5.0) * 40 + (brack / 5.0) * 30 + popularity * 30,
            2,
        )

        results.append(
            BrowseCard(
                name=row.name,
                oracle_id=row.oracle_id,
                type_line=row.type_line,
                archetype_score=row.arch_score,
                bracket_score=row.brack_score,
                functions=functions,
                is_game_changer=is_gc,
                edhrec_decks=row.num_decks,
                score=score,
            )
        )

    results.sort(key=lambda c: c.score, reverse=True)
    return results[:limit]
