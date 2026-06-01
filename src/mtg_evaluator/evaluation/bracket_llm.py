"""
LLM-based Commander bracket estimation.

Sends a compact deck summary to the LLM and returns a bracket (1-5) with reasoning.
Token budget: ~900-1100 tokens per call (input + output).

Hard floors from official WotC rules (deterministic, enforced after LLM call):
  0 Game Changers  → max bracket 2 (LLM can only assign 1 or 2)
  1-3 Game Changers → floor bracket 3
  4+ Game Changers  → floor bracket 4
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import anthropic

from mtg_evaluator.config import settings

if TYPE_CHECKING:
    from mtg_evaluator.evaluation.evaluator import ComboHit

_SYSTEM_PROMPT = """\
You are an expert Magic: The Gathering Commander bracket evaluator using the official Wizards of the Coast Commander Brackets system (October 2025 update).

BRACKETS — assign the HIGHEST bracket the deck qualifies for based on repeatable performance, not lucky draws:

B1 Exhibition: Theme/showcase over power. Substandard or highly thematic win conditions. Opponents always have agency. 11+ turn wins typical. No Game Changers allowed.
B2 Core: Unoptimized, telegraphed, fully disruptable wins. Incremental, slow resource accumulation. 9+ turn wins typical. No Game Changers allowed.
B3 Upgraded: Strong synergy, high card quality, real disruption. Wins from one big accumulated-resource turn. 7+ turn wins typical. Up to 3 Game Changers.
B4 Optimized: Fast, consistent, lethal. Tutors, fast mana, free disruption. Wins feel sudden and hard to stop. 4+ turn wins typical. Unlimited Game Changers.
B5 cEDH: Victory-first metagame optimization. Can end any turn. Intricate, maximally efficient. Unlimited Game Changers.

KEY RULES:
- Game Changer count is a hard floor: 4+ GCs = minimum B4; 1-3 GCs = minimum B3; 0 GCs = maximum B2.
- Combos that frequently fire before the bracket's expected turn window push the bracket up.
- Mass land denial (Armageddon, Ruination, etc.) or chained extra turns → B4 minimum.
- Assess the deck's repeatable gameplan, not its best-case hand.
- Opponent agency matters: B1-B3 decks give opponents time and counterplay windows.

Return ONLY the assign_bracket tool call. Reasoning must be 2 sentences maximum.\
"""

_TOOL_SCHEMA = {
    "name": "assign_bracket",
    "description": "Assign a Commander bracket 1-5 to this deck",
    "input_schema": {
        "type": "object",
        "properties": {
            "bracket": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": "Official bracket 1-5",
            },
            "reasoning": {
                "type": "string",
                "description": "2 sentences max. State the key factor that determined the bracket, then note what would change it.",
            },
        },
        "required": ["bracket", "reasoning"],
    },
}


@dataclass
class BracketResult:
    bracket: int           # 1-5
    bracket_label: str     # bracket_2, bracket_3, etc. (matches existing DB labels)
    reasoning: str
    game_changer_count: int
    game_changer_floor: int  # minimum bracket from GC rules


_INT_TO_LABEL = {1: "bracket_1", 2: "bracket_2", 3: "bracket_3", 4: "bracket_4", 5: "cedh"}
_GC_FLOOR = {0: 1, 1: 3, 2: 3, 3: 3}  # 0 GCs → floor 1; 1-3 → floor 3; 4+ → floor 4


def _gc_floor(gc_count: int) -> int:
    if gc_count >= 4:
        return 4
    return _GC_FLOOR.get(gc_count, 1)


def classify_bracket(
    commander: str,
    partner: str | None,
    archetype: str | None,
    game_changers_found: list[str],
    combos_found: list[ComboHit],
    mass_land_denial_found: list[str],
    extra_turns_found: list[str],
    role_coverage: dict[str, int],
    card_names: list[str],
) -> BracketResult:
    """Call the LLM to classify a deck into a bracket 1-5.

    Hard floors are computed here from deterministic signals and enforced after
    the LLM call — the LLM cannot assign below the floor, only above it.

    Hard B4 floors (pre-LLM):
      - 4+ Game Changers
      - Any Ruthless (R) or Spicy (S) combo present
      - Mass land denial card present
    Hard B3 floors:
      - 1-3 Game Changers
      - Any Powerful (P) combo present
    """
    gc_count = len(game_changers_found)

    # Compute hard floor from all deterministic signals
    floor = _gc_floor(gc_count)

    ruthless_combos = [c for c in combos_found if c.bracket_tag in ("R", "S")]
    powerful_combos = [c for c in combos_found if c.bracket_tag == "P"]

    floor_reasons: list[str] = []
    if gc_count >= 4:
        floor_reasons.append(f"{gc_count} Game Changers")
    elif gc_count >= 1:
        floor_reasons.append(f"{gc_count} Game Changer(s)")
    if ruthless_combos:
        floor = max(floor, 4)
        floor_reasons.append(f"{len(ruthless_combos)} Ruthless/Spicy combo(s)")
    if powerful_combos and floor < 3:
        floor = max(floor, 3)
        floor_reasons.append(f"{len(powerful_combos)} Powerful combo(s)")
    if mass_land_denial_found:
        floor = max(floor, 4)
        floor_reasons.append(f"mass land denial: {', '.join(mass_land_denial_found)}")

    # Build compact user message — all the hard evidence up front
    cmdr_line = commander + (f" + {partner}" if partner else "")
    gc_line = (f"{gc_count} Game Changers: {', '.join(game_changers_found)}"
               if game_changers_found else "0 Game Changers")

    combo_lines: list[str] = []
    for c in combos_found:
        combo_lines.append(
            f"  [{c.bracket_tag}] {' + '.join(c.card_names)}"
            + (f" → {c.results_description}" if c.results_description else "")
        )
    combo_section = ("Known combos present:\n" + "\n".join(combo_lines)) if combo_lines else "No known combos detected"

    land_denial_line = f"Mass land denial: {', '.join(mass_land_denial_found)}" if mass_land_denial_found else ""
    extra_turns_line = f"Extra turn cards: {', '.join(extra_turns_found)}" if extra_turns_found else ""
    hard_floor_line = f"HARD FLOOR: bracket {floor} (from: {'; '.join(floor_reasons)})" if floor_reasons else ""

    coverage_line = "  ".join(f"{k}={v}" for k, v in role_coverage.items())
    card_list = ", ".join(card_names)

    sections = [
        f"Commander: {cmdr_line}",
        f"Archetype guess: {archetype or 'unknown'}",
        gc_line,
        combo_section,
    ]
    if land_denial_line:
        sections.append(land_denial_line)
    if extra_turns_line:
        sections.append(extra_turns_line)
    if hard_floor_line:
        sections.append(hard_floor_line)
    sections += [
        f"Role coverage: {coverage_line}",
        f"\nDecklist ({len(card_names)} cards):\n{card_list}",
    ]

    user_msg = "\n".join(sections)

    client = anthropic.Anthropic(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_anthropic_base_url,
    )

    kwargs: dict = dict(
        model=settings.deepseek_model,
        max_tokens=256,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "assign_bracket"},
        messages=[{"role": "user", "content": user_msg}],
    )
    if "deepseek" in settings.deepseek_anthropic_base_url:
        kwargs["thinking"] = {"type": "disabled"}

    response = client.messages.create(**kwargs)

    # Extract tool call result
    tool_use = next(b for b in response.content if b.type == "tool_use")
    raw_bracket: int = tool_use.input["bracket"]
    reasoning: str = tool_use.input["reasoning"]

    # Enforce hard floor from GC count
    enforced = max(raw_bracket, floor)
    if enforced != raw_bracket:
        reasoning += f" (bracket raised from {raw_bracket} to {enforced} by Game Changer floor rule)"

    return BracketResult(
        bracket=enforced,
        bracket_label=_INT_TO_LABEL[enforced],
        reasoning=reasoning,
        game_changer_count=gc_count,
        game_changer_floor=floor,
    )
