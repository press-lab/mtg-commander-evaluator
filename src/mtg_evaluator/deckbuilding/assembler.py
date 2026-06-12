"""
Deck assembler: deterministic skeleton + bounded LLM refinement.

The deck is SOLVED deterministically first (skeleton.py): every role target
filled with the highest-scoring available cards, basics allocated by Karsten
color math, combos included as units. The LLM then reviews the baseline and
may propose a bounded number of swaps (each with a stated reason) from a list
of named alternatives. Invalid swaps are rejected; if the LLM is unavailable
the skeleton ships as-is.

This keeps the LLM as a synergy-judgment layer rather than the engine — a
weak or hallucinating model can only make small, validated changes, never a
broken deck.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import anthropic

from mtg_evaluator.card_functions import has_function, normalize_functions, role_bucket
from mtg_evaluator.config import settings
from mtg_evaluator.deckbuilding.pool import CardPool, PoolCard
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
from mtg_evaluator.deckbuilding.role_targets import RoleTargets
from mtg_evaluator.deckbuilding.skeleton import BASIC_LAND_NAMES, build_skeleton

_MAX_SWAPS = 12

_SYSTEM_PROMPT = """\
You are an expert Magic: The Gathering Commander deck builder reviewing a deck that was \
assembled by a deterministic optimizer. The baseline already satisfies role targets, mana \
math, and the requested bracket/style. Your job is SYNERGY JUDGMENT the optimizer can't see: \
cards that work especially well (or badly) with the commander's specific text, curve clumps, \
tribal/typal lines, and combo support.

RULES:
- Propose between 0 and {max_swaps} swaps. Zero swaps is a valid answer if the baseline is good.
- Each swap removes one baseline card and adds one card from the ALTERNATIVES list (verbatim names).
- Never swap away basic lands, combo pieces, or the cheapest ramp.
- A swap must keep the deck's role balance intact: if you remove a draw spell, the added card \
  should also draw (or the deck must already exceed its draw target).
- Each swap needs a concrete reason referencing the commander or gameplan — not "this card is good".

Return ONLY the refine_deck tool call.\
"""

_TOOL_SCHEMA = {
    "name": "refine_deck",
    "description": "Propose bounded swaps to refine a deterministically assembled Commander deck",
    "input_schema": {
        "type": "object",
        "properties": {
            "swaps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "remove": {
                            "type": "string",
                            "description": "Exact name of a baseline card to remove",
                        },
                        "add": {
                            "type": "string",
                            "description": "Exact name of an ALTERNATIVES card to add",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Concrete synergy reason for this swap",
                        },
                    },
                    "required": ["remove", "add", "reason"],
                },
                "description": "0 to 12 swaps against the baseline deck",
            },
            "reasoning": {
                "type": "string",
                "description": "2-4 sentences: overall assessment of the baseline and the theme of your changes (or why none were needed).",
            },
        },
        "required": ["swaps", "reasoning"],
    },
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
    role_targets: RoleTargets | None = None
    mana_analysis: ManaAnalysis | None = None
    consistency: ConsistencyReport | None = None
    package_health: PackageHealth | None = None
    nonbo_warnings: list[NonboWarning] = field(default_factory=list)
    repair_notes: list[str] = field(default_factory=list)
    # Per-card rationale from the deterministic solver + LLM swap reasons
    card_rationale: dict[str, str] = field(default_factory=dict)
    # Swaps the LLM made against the deterministic baseline
    llm_swaps: list[dict] = field(default_factory=list)

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
                # Aggregate duplicates (multiple basics) into one quantity line
                counts: dict[str, int] = {}
                for card in cards:
                    counts[card] = counts.get(card, 0) + 1
                for card, qty in counts.items():
                    lines.append(f"{qty} {card}")
                lines.append("")

        lines.append(f"// {self.total} cards total")
        if self.reasoning:
            for sentence in self.reasoning.split(". "):
                if sentence.strip():
                    lines.append(
                        f"// {sentence.strip()}{'.' if not sentence.strip().endswith('.') else ''}"
                    )

        return "\n".join(lines)


def validate_assembled_deck(
    deck: AssembledDeck, role_targets: RoleTargets | None = None
) -> list[str]:
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

    # 2. Duplicate detection across all categories (basics may repeat freely)
    seen: set[str] = set()
    dupes: set[str] = set()
    for name in deck.all_cards:
        if name in seen and name not in BASIC_LAND_NAMES:
            dupes.add(name)
        seen.add(name)
    if dupes:
        warnings.append(f"Duplicate cards selected: {', '.join(sorted(dupes))}")

    # 3. Role minimums. Prefer dynamic pool targets; fixed values are a fallback
    # for callers that validate an assembled deck outside the pool builder.
    target_map = role_targets.to_dict() if role_targets else None
    minimums = target_map or _VALIDATE_MINIMUMS.get(deck.bracket, _VALIDATE_MINIMUMS[3])
    for role, minimum in minimums.items():
        if role in {"board_wipe", "protection", "finisher"}:
            continue
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


def _category_for_card(card: PoolCard) -> str:
    """Best-effort category for repair fills using normalized functions."""
    type_line = card.type_line.lower()
    if "land" in type_line:
        return "lands"
    if has_function(card.functions, "ramp"):
        return "ramp"
    if has_function(card.functions, "draw"):
        return "draw"
    if (
        has_function(card.functions, "removal")
        or has_function(card.functions, "board_wipe")
        or has_function(card.functions, "counterspell")
    ):
        return "removal"
    if card.combo_ids or has_function(card.functions, "combo_piece"):
        return "combo"
    if any(
        has_function(card.functions, role)
        for role in ("token_maker", "sacrifice_outlet", "recursion", "finisher")
    ):
        return "synergy"
    return "other"


def _apply_swaps(
    deck: AssembledDeck,
    swaps: list[dict],
    alternatives_by_name: dict[str, PoolCard],
) -> None:
    """
    Apply LLM-proposed swaps against the skeleton baseline. Every swap is
    validated: the removed card must exist in the deck (and not be a basic
    land or combo piece), the added card must come from the alternatives
    list and not already be in the deck. Invalid swaps are skipped with a
    note — the LLM can never corrupt the deck.
    """
    notes: list[str] = []
    applied: list[dict] = []
    in_deck = set(deck.all_cards)
    categories = ("lands", "ramp", "draw", "removal", "synergy", "combo", "other")

    for swap in swaps[:_MAX_SWAPS]:
        remove = str(swap.get("remove", "")).strip()
        add = str(swap.get("add", "")).strip()
        reason = str(swap.get("reason", "")).strip()

        if not remove or not add:
            continue
        if remove in BASIC_LAND_NAMES:
            notes.append(f"Rejected swap: {remove} is a basic land")
            continue
        if remove in deck.combo:
            notes.append(f"Rejected swap: {remove} is a combo piece")
            continue
        if remove not in in_deck:
            notes.append(f"Rejected swap: {remove} is not in the deck")
            continue
        if add in in_deck:
            notes.append(f"Rejected swap: {add} is already in the deck")
            continue
        new_card = alternatives_by_name.get(add)
        if new_card is None:
            notes.append(f"Rejected swap: {add} is not in the alternatives list")
            continue

        # Remove from whichever category holds it
        for category in categories:
            cards = getattr(deck, category)
            if remove in cards:
                cards.remove(remove)
                break
        in_deck.discard(remove)

        target_category = _category_for_card(new_card)
        getattr(deck, target_category).append(add)
        in_deck.add(add)
        deck.card_rationale[add] = f"LLM swap for {remove}: {reason}"
        applied.append({"remove": remove, "add": add, "reason": reason})

    deck.llm_swaps = applied
    deck.repair_notes = notes


def _analyze_assembled_deck(deck: AssembledDeck, pool: CardPool) -> None:
    """Attach final-deck analyses using the selected 98/99, not the candidate pool."""
    cards_by_name = {card.name: card for card in pool.all_cards}
    selected_cards = [
        cards_by_name[name] for name in deck.all_cards if name in cards_by_name
    ]
    flat_functions: list[str] = []
    card_functions: dict[str, list[str]] = {}

    for card in selected_cards:
        functions = normalize_functions(card.functions)
        flat_functions.extend(functions)
        card_functions[card.name] = functions

    # All lands count — basics aren't pool cards but mana analysis knows them
    land_names = list(deck.lands)
    land_metadata = {
        card.name: LandManaData(
            produced_mana=tuple(card.produced_mana),
            oracle_text=card.oracle_text,
        )
        for card in selected_cards
        if "land" in card.type_line.lower()
    }
    ramp_count = sum(
        1 for card in selected_cards if has_function(card.functions, "ramp")
    )
    board_wipe_count = sum(
        1 for card in selected_cards if has_function(card.functions, "board_wipe")
    )
    interaction_count = sum(
        1
        for card in selected_cards
        if any(
            role_bucket(fn) in {"removal", "board_wipe", "counterspell"}
            for fn in normalize_functions(card.functions)
        )
    )
    creature_count = sum(
        1 for card in selected_cards if "creature" in card.type_line.lower()
    )
    nonland_cards = [
        card for card in selected_cards if "land" not in card.type_line.lower()
    ]
    avg_cmc = (
        sum(card.cmc for card in nonland_cards) / len(nonland_cards)
        if nonland_cards
        else 3.5
    )

    enabler_count = 0
    payoff_count = 0
    pkg = ARCHETYPE_PACKAGES.get(pool.archetype or "")
    if pkg:
        for card in selected_cards:
            functions = normalize_functions(card.functions)
            if any(fn in pkg.enabler_functions for fn in functions):
                enabler_count += 1
            if any(fn in pkg.payoff_functions for fn in functions):
                payoff_count += 1

    deck.role_targets = pool.role_targets
    deck.mana_analysis = analyze_mana_base(
        land_names=land_names,
        commander_mana_cost=pool.commander_mana_cost,
        commander_cmc=pool.commander_cmc,
        deck_color_identity=pool.color_identity,
        partner_mana_cost=pool.partner_mana_cost,
        land_metadata=land_metadata,
    )
    deck.consistency = compute_consistency(
        land_count=len(land_names),
        ramp_count=ramp_count,
        interaction_count=interaction_count,
        commander_cmc=pool.commander_cmc,
        enabler_count=enabler_count,
        payoff_count=payoff_count,
        deck_size=98 if deck.has_partner else 99,
    )
    deck.package_health = check_package_health(pool.archetype, flat_functions)
    deck.nonbo_warnings = detect_nonbos(
        archetype=pool.archetype,
        all_functions=flat_functions,
        all_card_names=list(card_functions),
        all_card_functions=card_functions,
        ramp_count=ramp_count,
        avg_cmc=avg_cmc,
        land_count=len(land_names),
        board_wipe_count=board_wipe_count,
        creature_count=creature_count,
    )


def assemble_deck(pool: CardPool, bracket: int = 3) -> AssembledDeck:
    """
    Solve the deck deterministically (skeleton), then let the LLM propose
    bounded, validated swaps for synergy judgment. If the LLM is unavailable
    or returns garbage, the deterministic skeleton ships unchanged.

    With a solo commander: 99 cards. With a partner pair: 98 cards.
    """
    has_partner = " + " in pool.commander_name
    targets = (
        pool.role_targets.to_dict()
        if pool.role_targets
        else _VALIDATE_MINIMUMS.get(bracket, _VALIDATE_MINIMUMS[3])
    )

    # --- 1. Deterministic solve ---
    skeleton = build_skeleton(pool)
    deck = AssembledDeck(
        commander=pool.commander_name,
        archetype=pool.archetype,
        bracket=bracket,
        has_partner=has_partner,
        lands=list(skeleton.lands),
        ramp=list(skeleton.ramp),
        draw=list(skeleton.draw),
        removal=list(skeleton.removal),
        synergy=list(skeleton.synergy),
        combo=list(skeleton.combo),
        other=list(skeleton.other),
        card_rationale=dict(skeleton.rationale),
    )

    # --- 2. Alternatives: best pool cards NOT in the baseline ---
    in_deck = set(deck.all_cards)
    unpicked = [c for c in pool.all_cards if c.name not in in_deck]
    unpicked.sort(key=lambda c: c.score, reverse=True)
    alt_nonland = [c for c in unpicked if "land" not in c.type_line.lower()][:50]
    alt_land = [c for c in unpicked if "land" in c.type_line.lower()][:10]
    alternatives = alt_nonland + alt_land
    alternatives_by_name = {c.name: c for c in alternatives}

    # --- 3. LLM refinement (bounded swaps) ---
    try:
        deck.reasoning = _llm_refine(pool, deck, alternatives, targets, bracket)
    except Exception as exc:  # LLM down/misbehaving → deterministic build ships
        deck.reasoning = (
            "Deterministic build — every role target filled with the "
            "highest-scoring available cards. (LLM refinement unavailable: "
            f"{type(exc).__name__})"
        )

    _analyze_assembled_deck(deck, pool)
    return deck


def _llm_refine(
    pool: CardPool,
    deck: AssembledDeck,
    alternatives: list[PoolCard],
    targets: dict,
    bracket: int,
) -> str:
    """One LLM call: review the baseline, return validated swaps + reasoning."""

    def _fmt_card(c: PoolCard) -> str:
        fns = ",".join(c.functions[:3]) if c.functions else "—"
        gc = " [GC]" if c.is_game_changer else ""
        combo = " [COMBO]" if c.combo_ids else ""
        syn = (
            f" syn={c.commander_synergy:+.2f}"
            if c.commander_synergy is not None
            else ""
        )
        return f"  {c.name}{gc}{combo} | {fns} | score={c.score:.0f}{syn}"

    cards_by_name = {c.name: c for c in pool.all_cards}

    def _baseline_section(label: str, names: list[str]) -> str:
        if not names:
            return ""
        counts: dict[str, int] = {}
        for n in names:
            counts[n] = counts.get(n, 0) + 1
        lines = []
        for n, qty in counts.items():
            prefix = f"{qty}x " if qty > 1 else ""
            card = cards_by_name.get(n)
            fns = ",".join(card.functions[:2]) if card and card.functions else ""
            lines.append(f"  {prefix}{n}" + (f" ({fns})" if fns else ""))
        return f"{label} ({len(names)}):\n" + "\n".join(lines)

    baseline = "\n".join(
        section
        for section in (
            _baseline_section("LANDS", deck.lands),
            _baseline_section("RAMP", deck.ramp),
            _baseline_section("DRAW", deck.draw),
            _baseline_section("REMOVAL", deck.removal),
            _baseline_section("SYNERGY", deck.synergy),
            _baseline_section("COMBO", deck.combo),
            _baseline_section("OTHER", deck.other),
        )
        if section
    )

    combo_lines = [
        f"  [{combo.bracket_tag}] {' + '.join(combo.card_names)}"
        + (f" → {combo.results_description}" if combo.results_description else "")
        for combo in pool.combos[:6]
    ]
    combo_section = (
        ("\nKnown combos:\n" + "\n".join(combo_lines)) if combo_lines else ""
    )

    profile_section = ""
    if pool.commander_profile and pool.commander_profile.confidence > 0:
        p = pool.commander_profile
        profile_section = (
            f"\nCommander profile: provides [{', '.join(p.provides_list) or 'nothing'}]; "
            f"needs [{', '.join(p.needs_list) or 'nothing specific'}]; "
            f"dependency {p.dependency_score:.0f}/5"
        )

    style = pool.request.build_style
    style_note = {
        "optimized": "Style: OPTIMIZED — converge on proven meta picks; only swap toward higher-synergy staples.",
        "balanced": "Style: BALANCED — quality first; swaps should improve commander-specific synergy.",
        "spicy": "Style: SPICY — the user wants underplayed cards; do NOT swap toward popular staples.",
    }.get(style, "")

    role_target_line = (
        f"Role targets (already satisfied): {targets.get('lands', 36)} lands, "
        f"{targets.get('ramp', 10)} ramp, {targets.get('draw', 8)} draw, "
        f"{targets.get('removal', 6)} removal, {targets.get('board_wipe', 2)} wipes, "
        f"{targets.get('protection', 2)} protection, {targets.get('finisher', 3)} finishers"
    )

    user_msg = f"""Commander: {pool.commander_name}
Archetype: {pool.archetype or "general goodstuff"}
Colors: {", ".join(pool.color_identity) or "Colorless"}
Bracket: {_BRACKET_DESC.get(bracket, f"B{bracket}")}
{style_note}
{role_target_line}{profile_section}{combo_section}

BASELINE DECK ({deck.total} cards, deterministically optimized):
{baseline}

ALTERNATIVES (you may only add from this list):
{chr(10).join(_fmt_card(c) for c in alternatives)}

Review the baseline for commander-specific synergy. Propose 0-{_MAX_SWAPS} swaps."""

    client = anthropic.Anthropic(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_anthropic_base_url,
    )
    kwargs: dict = dict(
        model=settings.deepseek_model,
        max_tokens=1536,
        system=_SYSTEM_PROMPT.format(max_swaps=_MAX_SWAPS),
        tools=[_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "refine_deck"},
        messages=[{"role": "user", "content": user_msg}],
    )
    if "deepseek" in settings.deepseek_anthropic_base_url:
        kwargs["thinking"] = {"type": "disabled"}

    response = client.messages.create(**kwargs)
    tool_use = next(b for b in response.content if b.type == "tool_use")
    inp = tool_use.input

    alternatives_by_name = {c.name: c for c in alternatives}
    _apply_swaps(deck, list(inp.get("swaps", []) or []), alternatives_by_name)

    reasoning = str(inp.get("reasoning", "")).strip()
    if deck.llm_swaps:
        return reasoning or f"Refined the deterministic baseline with {len(deck.llm_swaps)} synergy swaps."
    return reasoning or "Deterministic baseline confirmed — no swaps needed."
