"""
LLM-based deck assembler.

Takes a scored CardPool (~200 candidates) and uses DeepSeek to select
exactly 99 cards (commander slot is the 100th), organized into a complete
Commander deck with mana curve awareness, role balance, and synergy reasoning.

Token budget: ~2,500-3,500 input, ~800-1,000 output per call.

The LLM receives:
  - Commander(s), archetype, bracket, color identity
  - Role targets (ramp/draw/removal counts)
  - Top ~120 candidates with tier, functions, score
  - Any known combos

It returns a structured tool call with cards grouped by role, plus
a 3-5 sentence explanation of the key choices.

Lands are handled separately — we pre-select the best available lands
from the pool, then the LLM fills the remaining 62-65 nonland slots.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import anthropic

from mtg_evaluator.config import settings
from mtg_evaluator.deckbuilding.pool import CardPool, PoolCard

_SYSTEM_PROMPT = """\
You are an expert Magic: The Gathering Commander deck builder. Given a scored candidate pool, \
select exactly the right cards to build a tight, coherent 99-card Commander deck.

GUIDELINES:
- Target mana curve: ~35-38 nonbasic + basic lands, 10-12 ramp pieces, 8-10 draw, 5-7 removal, \
  1-3 board wipes, 2-3 protection pieces. The rest fills the archetype gameplan.
- Prefer cards that do double duty (ramp + on-theme, draw + synergy).
- Avoid redundancy: if you already have Demonic Tutor, you may not need Vampiric Tutor unless \
  tutors ARE the strategy.
- Respect bracket: at B1-B2 avoid fast mana and one-sided effects; at B4-B5 prioritize consistency.
- Include combo pieces together if a combo fits the deck.
- CRITICAL: Return EXACTLY the number of cards requested across all categories. Every card name \
  must be taken verbatim from the candidate pool list provided. Do not invent cards.

Return ONLY the assemble_deck tool call.\
"""

_TOOL_SCHEMA = {
    "name": "assemble_deck",
    "description": "Select the final cards for a Commander deck from the provided candidate pool",
    "input_schema": {
        "type": "object",
        "properties": {
            "lands": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Land cards (nonbasic + basics). Target 36-38 total. Only cards from the pool.",
            },
            "ramp": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ramp / mana acceleration. Target 10-12 cards.",
            },
            "draw": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Card draw / advantage. Target 8-10 cards.",
            },
            "removal": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Single-target removal + board wipes. Target 6-8 cards.",
            },
            "synergy": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Core archetype / synergy pieces — the heart of the gameplan.",
            },
            "combo": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Combo pieces (may be empty). Only include if combos are wanted.",
            },
            "other": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Remaining cards that round out the deck.",
            },
            "reasoning": {
                "type": "string",
                "description": "3-5 sentences. Explain the deck's gameplan, key synergies, and why specific high-impact cards were included or excluded.",
            },
        },
        "required": [
            "lands",
            "ramp",
            "draw",
            "removal",
            "synergy",
            "combo",
            "other",
            "reasoning",
        ],
    },
}

# Role targets by bracket.
# B4/B5 run significantly fewer basic lands because fast mana (Sol Ring, Mana Vault,
# Chrome Mox, etc.) fills the gap. Total mana sources stays ~40; it's just the
# land/rock split that shifts.
_ROLE_TARGETS = {
    1: dict(lands=38, ramp=10, draw=8, removal=6),
    2: dict(lands=37, ramp=11, draw=9, removal=6),
    3: dict(lands=36, ramp=12, draw=10, removal=7),
    4: dict(lands=32, ramp=14, draw=10, removal=6),  # fast mana replaces 3-4 land slots
    5: dict(
        lands=29, ramp=16, draw=11, removal=5
    ),  # cEDH: maximize fast mana & interaction
}

# Validation minimums per bracket (what we'll warn on if underfilled).
# Lands + ramp should sum to ~42 at any bracket for consistent mana.
_VALIDATE_MINIMUMS = {
    1: dict(lands=36, ramp=8, draw=6, removal=4),
    2: dict(lands=35, ramp=9, draw=7, removal=4),
    3: dict(lands=33, ramp=10, draw=8, removal=5),
    4: dict(
        lands=28, ramp=12, draw=8, removal=4
    ),  # fast mana compensates for fewer lands
    5: dict(
        lands=25, ramp=14, draw=9, removal=4
    ),  # cEDH: 25+ lands is fine with 14+ rocks
}

_BRACKET_DESC = {
    1: "B1 Exhibition — casual/theme deck, wide win windows, no fast mana",
    2: "B2 Core — precon power level, telegraphed wins",
    3: "B3 Upgraded — focused synergy, real interaction",
    4: "B4 Optimized — fast and consistent, tutors + fast mana welcome",
    5: "B5 cEDH — maximum efficiency, stack-combat expected",
}


@dataclass
class AssembledDeck:
    commander: str
    archetype: str | None
    bracket: int
    has_partner: bool = False  # True when commander is a pair (e.g. "Tymna + Thrasios")

    lands: list[str] = field(default_factory=list)
    ramp: list[str] = field(default_factory=list)
    draw: list[str] = field(default_factory=list)
    removal: list[str] = field(default_factory=list)
    synergy: list[str] = field(default_factory=list)
    combo: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)

    reasoning: str = ""

    @property
    def all_cards(self) -> list[str]:
        return (
            self.lands
            + self.ramp
            + self.draw
            + self.removal
            + self.synergy
            + self.combo
            + self.other
        )

    @property
    def total(self) -> int:
        return len(self.all_cards)

    def to_moxfield(self) -> str:
        """Export as Moxfield-compatible text with section comments."""
        cmd_parts = self.commander.split(" + ")
        lines = ["Commander"]
        for cmd in cmd_parts:
            lines.append(f"1 {cmd.strip()}")
        lines.append("")
        lines.append("Deck")

        sections = [
            ("Lands", self.lands),
            ("Ramp", self.ramp),
            ("Draw", self.draw),
            ("Removal", self.removal),
            ("Synergy", self.synergy),
            ("Combo", self.combo),
            ("Other", self.other),
        ]
        for label, cards in sections:
            if cards:
                lines.append(f"// ── {label} ({len(cards)}) ──")
                for card in cards:
                    lines.append(f"1 {card}")
                lines.append("")

        lines.append(f"// {self.total} cards total")
        if self.reasoning:
            for sentence in self.reasoning.split(". "):
                if sentence.strip():
                    lines.append(
                        f"// {sentence.strip()}{'.' if not sentence.strip().endswith('.') else ''}"
                    )

        return "\n".join(lines)


def validate_assembled_deck(deck: AssembledDeck) -> list[str]:
    """
    Post-assembly sanity checks. Returns a list of warning strings.
    Empty list means the deck passes all checks.

    Expected total is 98 with a partner pair, 99 with a solo commander.
    Land minimums scale down at B4/B5 where fast mana fills the gap.
    """
    warnings: list[str] = []
    expected_total = 98 if deck.has_partner else 99

    # 1. Total card count
    if deck.total != expected_total:
        warnings.append(
            f"Card count is {deck.total} (expected {expected_total} for "
            f"{'partner pair' if deck.has_partner else 'solo commander'}). "
            "The AI may have under- or over-selected."
        )

    # 2. Duplicate detection across all categories
    seen: set[str] = set()
    dupes: set[str] = set()
    for name in deck.all_cards:
        if name in seen:
            dupes.add(name)
        seen.add(name)
    if dupes:
        warnings.append(f"Duplicate cards selected: {', '.join(sorted(dupes))}")

    # 3. Bracket-aware role minimums
    minimums = _VALIDATE_MINIMUMS.get(deck.bracket, _VALIDATE_MINIMUMS[3])
    for role, minimum in minimums.items():
        count = len(getattr(deck, role, []))
        if count < minimum:
            if role == "lands":
                ramp_count = len(deck.ramp)
                if count + ramp_count < minimum + 8:
                    # Only warn on lands if mana rocks don't compensate
                    warnings.append(
                        f"Low lands: {count} (min {minimum} at B{deck.bracket}). "
                        f"Total mana sources with ramp: {count + ramp_count}."
                    )
            else:
                warnings.append(
                    f"Low {role}: {count} selected (min {minimum} at B{deck.bracket})"
                )

    # 4. No empty card names
    empty = [
        cat
        for cat in ("lands", "ramp", "draw", "removal", "synergy", "combo", "other")
        if any(not n.strip() for n in (getattr(deck, cat) or []))
    ]
    if empty:
        warnings.append(f"Empty card names in categories: {', '.join(empty)}")

    return warnings


def assemble_deck(pool: CardPool, bracket: int = 3) -> AssembledDeck:
    """
    Call DeepSeek to select the final deck from the scored pool.
    With a solo commander: 99 cards. With a partner pair: 98 cards.
    Returns an AssembledDeck with cards grouped by role.
    """
    has_partner = " + " in pool.commander_name
    deck_size = 98 if has_partner else 99
    targets = _ROLE_TARGETS.get(bracket, _ROLE_TARGETS[3])
    total_nonland = deck_size - targets["lands"]

    # Build candidate list — top 120 by score, show tier + functions
    all_candidates = pool.core + pool.support + pool.flex
    all_candidates.sort(key=lambda c: c.score, reverse=True)
    candidates = all_candidates[:120]

    # Separate land candidates (for transparency in prompt)
    land_candidates = [c for c in candidates if "land" in c.type_line.lower()]
    nonland_candidates = [c for c in candidates if "land" not in c.type_line.lower()]

    def _fmt_card(c: PoolCard) -> str:
        fns = ",".join(c.functions[:3]) if c.functions else "—"
        gc = " [GC]" if c.is_game_changer else ""
        combo = " [COMBO]" if c.combo_ids else ""
        return f"  {c.name}{gc}{combo} | {c.tier} | {fns} | score={c.score:.0f}"

    land_section = (
        "\n".join(_fmt_card(c) for c in land_candidates[:40]) or "  (no lands in pool)"
    )
    nonland_section = "\n".join(_fmt_card(c) for c in nonland_candidates[:100])

    # Combo summary
    combo_lines = []
    for combo in pool.combos[:8]:
        combo_lines.append(
            f"  [{combo.bracket_tag}] {' + '.join(combo.card_names)}"
            + (f" → {combo.results_description}" if combo.results_description else "")
        )
    combo_section = (
        ("\nKnown combos in pool:\n" + "\n".join(combo_lines)) if combo_lines else ""
    )

    role_target_line = (
        f"Role targets: {targets['lands']} lands, {targets['ramp']} ramp, "
        f"{targets['draw']} draw, {targets['removal']} removal, "
        f"~{total_nonland} total nonland"
    )
    want_combos = "YES — include combo pieces" if pool.combos else "No combos requested"

    user_msg = f"""Commander: {pool.commander_name}
Archetype: {pool.archetype or "general goodstuff"}
Colors: {", ".join(pool.color_identity) or "Colorless"}
Bracket: {_BRACKET_DESC.get(bracket, f"B{bracket}")}
{role_target_line}
Combos: {want_combos}
{combo_section}

CANDIDATE POOL ({len(candidates)} cards — pick from these only):

LANDS ({len(land_candidates)} available):
{land_section}

NONLANDS ({len(nonland_candidates)} available):
{nonland_section}

Select exactly {deck_size} cards total across all categories. Card names must match exactly."""

    client = anthropic.Anthropic(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_anthropic_base_url,
    )

    kwargs: dict = dict(
        model=settings.deepseek_model,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "assemble_deck"},
        messages=[{"role": "user", "content": user_msg}],
    )
    if "deepseek" in settings.deepseek_anthropic_base_url:
        kwargs["thinking"] = {"type": "disabled"}

    response = client.messages.create(**kwargs)

    tool_use = next(b for b in response.content if b.type == "tool_use")
    inp = tool_use.input

    # Validate — remove any cards not in candidate pool (hallucinations)
    valid_names = {c.name for c in candidates}

    def _clean(names: list) -> list[str]:
        out = []
        for n in names or []:
            name = str(n).strip()
            if name in valid_names:
                out.append(name)
        return out

    deck = AssembledDeck(
        commander=pool.commander_name,
        archetype=pool.archetype,
        bracket=bracket,
        has_partner=has_partner,
        lands=_clean(inp.get("lands", [])),
        ramp=_clean(inp.get("ramp", [])),
        draw=_clean(inp.get("draw", [])),
        removal=_clean(inp.get("removal", [])),
        synergy=_clean(inp.get("synergy", [])),
        combo=_clean(inp.get("combo", [])),
        other=_clean(inp.get("other", [])),
        reasoning=inp.get("reasoning", ""),
    )

    return deck
