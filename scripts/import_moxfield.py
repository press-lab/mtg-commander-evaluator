"""
Import decks from a Moxfield JSON export (downloaded via browser console)
and run evaluate-deck on each one.
"""
import io
import json
import sys
from pathlib import Path

# Force UTF-8 stdout so Unicode bar chars render on Windows
if hasattr(sys.stdout, "buffer") and sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from mtg_evaluator.db.connection import get_session
from mtg_evaluator.evaluation.parser import parse_decklist, ParsedDecklist
from mtg_evaluator.evaluation.evaluator import evaluate_decklist, EvaluationResult


def moxfield_to_text(deck: dict) -> str:
    lines = []
    for name in deck.get("commanders", {}).keys():
        lines.append(f"1 {name} *CMDR*")
    for name, entry in deck.get("mainboard", {}).items():
        qty = entry.get("quantity", 1)
        lines.append(f"{qty} {name}")
    return "\n".join(lines)


def print_result(deck_name: str, result: EvaluationResult) -> None:
    print(f"\n{'='*60}")
    print(f"  {deck_name}")
    print(f"{'='*60}")
    if result.commander_name:
        print(f"  Commander  : {result.commander_name}")
    print(f"  Archetype  : {result.archetype_guess or 'unknown'}")
    gc = result.game_changers_found
    print(f"  Bracket    : {result.bracket_estimate or 'unknown'}  ({len(gc)} game changers: {', '.join(gc) if gc else 'none'})")
    if result.bracket_reasoning:
        print(f"  Reasoning  : {result.bracket_reasoning}")
    print(f"  Synergy    : {result.synergy_score}/5" if result.synergy_score else "  Synergy    : n/a")

    print(f"\n  Role Coverage:")
    for rc in result.role_coverage:
        bar = "█" * rc.count + "░" * max(0, rc.minimum - rc.count)
        status = "✓" if rc.meets_minimum else f"✗ need {rc.gap} more"
        print(f"    {rc.function:<22} {rc.count:>2}/{rc.minimum}  {bar[:15]}  {status}")

    if result.gaps:
        print(f"\n  Gaps:")
        for g in result.gaps:
            print(f"    ! {g}")

    if result.improvement_suggestions:
        print(f"\n  Top Suggestions ({result.archetype_guess}):")
        for s in result.improvement_suggestions[:5]:
            print(f"    + {s['name']} (score {s['archetype_score']}/5)")

    if result.unresolved_cards:
        print(f"\n  Unresolved ({len(result.unresolved_cards)}): {', '.join(result.unresolved_cards[:5])}")
    if result.unclassified_cards:
        print(f"  Unclassified: {len(result.unclassified_cards)} cards")

    print(f"\n  Saved: {result.decklist_id}")


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\sethp\Downloads\moxfield_decks.json")
    decks = json.loads(path.read_text(encoding="utf-8"))
    print(f"Loaded {len(decks)} decks from {path.name}")

    with get_session() as session:
        for deck in decks:
            name = deck.get("name", "Unknown")
            raw = moxfield_to_text(deck)
            try:
                parsed = parse_decklist(raw, session)
                result = evaluate_decklist(session, parsed, deck_name=name)
                print_result(name, result)
            except Exception as e:
                print(f"\n  ERROR on {name}: {e}")


if __name__ == "__main__":
    main()
