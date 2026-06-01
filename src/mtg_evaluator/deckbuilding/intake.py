"""
Intake flow — ask the user the minimum questions needed to resolve a DeckRequest.

Skips questions that are obvious from context:
  - Single-archetype commanders → skip archetype question
  - B1/B2 → skip combo and tutor questions
  - Archetype passed in free text → use it, skip the question

Used by CLI interactively, and later by API/chat with the same logic.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from mtg_evaluator.db.models import Card, CardArchetypeScore, CardClassification
from mtg_evaluator.deckbuilding.request import DeckRequest, TutorDensity


def _top_archetypes(session: Session, oracle_id: str, top_n: int = 5) -> list[tuple[str, float]]:
    """Return the top N archetypes by total score for a given commander oracle_id."""
    cls_row = session.execute(
        select(CardClassification.id)
        .where(CardClassification.oracle_id == oracle_id)
        .where(CardClassification.is_valid == True)  # noqa: E712
        .order_by(CardClassification.classified_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if not cls_row:
        return []

    rows = session.execute(
        select(CardArchetypeScore.archetype, CardArchetypeScore.score)
        .where(CardArchetypeScore.classification_id == cls_row)
        .where(CardArchetypeScore.score >= 3)
        .order_by(CardArchetypeScore.score.desc())
        .limit(top_n)
    ).all()

    return [(r.archetype, r.score) for r in rows]


def _ask(prompt: str, options: list[str] | None = None) -> str:
    """Simple interactive prompt. In API mode this would be replaced."""
    if options:
        formatted = "  ".join(f"[{i+1}] {o}" for i, o in enumerate(options))
        print(f"\n{prompt}\n  {formatted}")
        while True:
            raw = input("  → ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1]
            # Allow typing the option name directly
            matches = [o for o in options if o.lower().startswith(raw.lower())]
            if len(matches) == 1:
                return matches[0]
            print(f"  Please enter a number 1-{len(options)} or option name.")
    else:
        print(f"\n{prompt}")
        return input("  → ").strip()


def resolve_intake(
    session: Session,
    commander_name: str,
    bracket: int | None = None,
    archetype: str | None = None,
    want_combos: bool | None = None,
    tutor_density: str | None = None,
    interactive: bool = True,
) -> DeckRequest:
    """
    Resolve a DeckRequest, asking only for what's missing.
    If interactive=False, uses defaults for anything unspecified (for API use).
    """
    # Resolve commander
    commander = session.scalars(
        select(Card).where(Card.name == commander_name)
    ).first()
    if not commander:
        # Try case-insensitive prefix match
        from sqlalchemy import func
        commander = session.scalars(
            select(Card)
            .where(func.lower(Card.name).like(f"{commander_name.lower()}%"))
            .where(Card.is_legendary == True)  # noqa: E712
            .limit(1)
        ).first()
    if not commander:
        raise ValueError(f"Commander '{commander_name}' not found in database.")

    print(f"\n  Commander: {commander.name}")
    print(f"  Colors: {' '.join(commander.color_identity or ['C'])}")

    # --- Bracket ---
    if bracket is None:
        if interactive:
            choice = _ask(
                "What bracket are you building for?",
                ["1 — Exhibition (casual theme)", "2 — Core (precon level)",
                 "3 — Upgraded (focused synergy)", "4 — Optimized (fast/consistent)",
                 "5 — cEDH (competitive)"],
            )
            bracket = int(choice[0])
        else:
            bracket = 3

    # --- Archetype ---
    if archetype is None:
        archetypes = _top_archetypes(session, commander.oracle_id)
        if len(archetypes) <= 1:
            archetype = archetypes[0][0] if archetypes else None
            if archetype:
                print(f"  Archetype: {archetype} (auto-selected — only viable option)")
        elif interactive:
            options = [f"{a} (score {s:.1f}/5)" for a, s in archetypes] + ["other (type below)"]
            choice = _ask(
                f"Which playstyle? {commander.name} supports these archetypes:",
                options,
            )
            if choice.startswith("other"):
                archetype = _ask("Enter archetype name:").lower().strip()
            else:
                archetype = choice.split(" ")[0]
        else:
            archetype = archetypes[0][0] if archetypes else None

    # --- Combos (only relevant for B3+) ---
    if want_combos is None:
        if bracket <= 2:
            want_combos = False   # B1/B2: no combo question, combos are casual-only anyway
        elif interactive:
            choice = _ask(
                "Do you want combos included?",
                ["yes", "no"],
            )
            want_combos = choice == "yes"
        else:
            want_combos = True

    # --- Tutor density (only relevant for B3+) ---
    valid_tutors: list[TutorDensity] = ["none", "light", "heavy"]
    if tutor_density is None or tutor_density not in valid_tutors:
        if bracket <= 2:
            tutor_density = "none"
        elif bracket == 3:
            tutor_density = "light"
        elif interactive:
            choice = _ask(
                "Tutor density?",
                ["none — no tutors", "light — 1-3 efficient tutors", "heavy — tutor-dense"],
            )
            tutor_density = choice.split(" ")[0]  # type: ignore[assignment]
        else:
            tutor_density = "light"

    # --- Optional notes ---
    notes: str | None = None
    if interactive:
        raw = _ask("Any specific themes, restrictions, or notes? (press Enter to skip)")
        notes = raw if raw else None

    return DeckRequest(
        commander_name=commander.name,
        bracket=bracket,
        archetype=archetype,
        want_combos=want_combos,
        tutor_density=tutor_density,  # type: ignore[arg-type]
        free_text_notes=notes,
    )
