"""
Archetype-first entry point: given an archetype (and optional color/bracket filters),
return ranked commanders that support it.

This is the starting point when a user knows WHAT they want to build
but not WHO to build it around.

Examples:
  "I want to build a sacrifice deck"
  "I want to build Goblin tribal"
  "Show me commanders for a B3 tokens deck in black/white"
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from mtg_evaluator.db.models import (
    Card, CardClassification, CardArchetypeScore, EDHRecCardStats,
)
from mtg_evaluator.evaluation.evaluator import GAME_CHANGERS


@dataclass
class CommanderOption:
    name: str
    oracle_id: str
    color_identity: list[str]
    archetype_score: float
    edhrec_decks: int | None
    type_line: str


def find_commanders(
    session: Session,
    archetype: str,
    colors: list[str] | None = None,      # filter to commanders within these colors
    max_colors: int | None = None,         # e.g. 2 = only mono/two-color commanders
    bracket: int | None = None,            # informational only — no hard filter
    limit: int = 20,
) -> list[CommanderOption]:
    """
    Return commanders ranked by archetype fit score.

    colors filter: if provided, only show commanders whose color identity
    is a SUBSET of the given colors (so a mono-G commander fits in a BRG filter).
    """
    # Get the latest valid classification per card, then join archetype scores
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
            EDHRecCardStats.num_decks,
        )
        .join(cls_subq, cls_subq.c.oracle_id == Card.oracle_id)
        .join(CardArchetypeScore,
              (CardArchetypeScore.classification_id == cls_subq.c.id) &
              (CardArchetypeScore.archetype == archetype))
        .join(EDHRecCardStats, EDHRecCardStats.oracle_id == Card.oracle_id, isouter=True)
        .where(Card.is_legendary == True)  # noqa: E712
        .where(Card.is_creature == True)   # legendary creatures only (most commanders)
        .where(CardArchetypeScore.score >= 3)
        .order_by(CardArchetypeScore.score.desc(), EDHRecCardStats.num_decks.desc().nullslast())
        .limit(limit * 4)   # over-fetch for color filtering
    ).all()

    color_filter = set(colors) if colors else None
    results: list[CommanderOption] = []

    for row in rows:
        ci = set(row.color_identity or [])

        # Color filter: commander's identity must be subset of requested colors
        if color_filter and not ci.issubset(color_filter | {"C"}):
            continue

        # Max colors filter
        if max_colors is not None and len(ci - {"C"}) > max_colors:
            continue

        results.append(CommanderOption(
            name=row.name,
            oracle_id=row.oracle_id,
            color_identity=sorted(row.color_identity or []),
            archetype_score=row.arch_score,
            edhrec_decks=row.num_decks,
            type_line=row.type_line,
        ))

        if len(results) >= limit:
            break

    return results
