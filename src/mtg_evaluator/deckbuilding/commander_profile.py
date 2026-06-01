"""
Commander profile generation and caching.

One LLM call (DeepSeek tool-use) per commander, result cached in commander_profiles.
A profile describes what the commander PROVIDES (reducing deck need) and what it
NEEDS (increasing deck requirements), plus risk scores for dependency/protection.

Cache invalidation: prompt_version field. If CURRENT_PROMPT_VERSION doesn't match
the stored version, the profile is regenerated. Manual overrides are never touched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import anthropic
from sqlalchemy.orm import Session

from mtg_evaluator.config import settings
from mtg_evaluator.db.models import Card, CommanderProfile as DBCommanderProfile

CURRENT_PROMPT_VERSION = "v1"

_PROFILE_TOOL: dict = {
    "name": "commander_profile",
    "description": "Structured deck-building profile for a Magic: The Gathering commander.",
    "input_schema": {
        "type": "object",
        "properties": {
            # Provides
            "provides_draw":             {"type": "boolean", "description": "Commander directly draws cards or creates repeatable card advantage for its controller."},
            "provides_ramp":             {"type": "boolean", "description": "Commander produces mana, reduces costs, or generates mana rocks/dorks."},
            "provides_tokens":           {"type": "boolean", "description": "Commander creates creature tokens as a primary or strong secondary function."},
            "provides_sac_outlet":       {"type": "boolean", "description": "Commander itself is a free or cheap sacrifice outlet."},
            "provides_recursion":        {"type": "boolean", "description": "Commander retrieves cards from graveyard to hand/battlefield/library."},
            "provides_graveyard_access": {"type": "boolean", "description": "Commander uses the graveyard as a resource (reanimation, flashback, delve, etc.)."},
            "provides_exile_access":     {"type": "boolean", "description": "Commander exiles cards and lets the controller cast or use them."},
            "provides_cost_reduction":   {"type": "boolean", "description": "Commander reduces the cost of spells or abilities."},
            "provides_removal":          {"type": "boolean", "description": "Commander removes permanents, counters spells, or disrupts opponents."},
            "provides_protection":       {"type": "boolean", "description": "Commander grants protection, hexproof, indestructible, or similar to itself or key pieces."},
            "provides_wincon":           {"type": "boolean", "description": "Commander IS the win condition or enables a game-ending combo without needing many support pieces."},
            "provides_combo_piece":      {"type": "boolean", "description": "Commander is one piece of a two-card or compact combo."},
            # Needs
            "needs_creatures":              {"type": "boolean", "description": "Commander requires a critical mass of creatures to function (lords, aristocrats triggers, etc.)."},
            "needs_artifacts":              {"type": "boolean", "description": "Commander synergizes specifically with artifacts and needs a high artifact count."},
            "needs_spells":                 {"type": "boolean", "description": "Commander rewards casting instants/sorceries and needs a high spell count."},
            "needs_lands":                  {"type": "boolean", "description": "Commander rewards land drops, landfall, or needs lands in specific zones."},
            "needs_combat":                 {"type": "boolean", "description": "Commander requires creatures dealing combat damage or attacking to trigger."},
            "needs_attack_damage_triggers": {"type": "boolean", "description": "Commander has attack/damage triggers that only function when attacking or dealing damage."},
            # Risk scores
            "dependency_score":  {
                "type": "number",
                "description": "0-5: how much the deck falls apart without the commander in play. 0=deck works fine without it, 5=deck is nearly non-functional.",
            },
            "protection_need": {
                "type": "number",
                "description": "0-5: how urgently the deck needs protection spells for the commander. 0=commander is replaceable, 5=must protect at all costs.",
            },
            "recast_importance": {
                "type": "number",
                "description": "0-5: how badly repeated removal hurts (commander tax). 0=free or cheap recast is fine, 5=recast cost cripples the deck.",
            },
            # Archetypes
            "preferred_archetypes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1-3 canonical archetype names this commander is best suited for. Choose from: midrange, control, combat, tokens, tribal, aristocrats, toolbox, big_mana, sacrifice, spellslinger, voltron, reanimator, graveyard, artifacts, blink, enchantress, equipment, treasure, lifegain, lands, group_slug, stax, pillow_fort, exile_matters, group_hug.",
            },
            "confidence": {
                "type": "number",
                "description": "0-1: your confidence in this profile. Use < 0.7 for complex or ambiguous commanders.",
            },
        },
        "required": [
            "provides_draw", "provides_ramp", "provides_tokens", "provides_sac_outlet",
            "provides_recursion", "provides_graveyard_access", "provides_exile_access",
            "provides_cost_reduction", "provides_removal", "provides_protection",
            "provides_wincon", "provides_combo_piece",
            "needs_creatures", "needs_artifacts", "needs_spells", "needs_lands",
            "needs_combat", "needs_attack_damage_triggers",
            "dependency_score", "protection_need", "recast_importance",
            "preferred_archetypes", "confidence",
        ],
    },
}

_SYSTEM_PROMPT = """\
You are an expert Magic: The Gathering Commander deck-building analyst. Given a commander's \
oracle text and characteristics, produce a precise structured profile describing what the \
commander contributes to a deck and what it demands. Be accurate and concise. Do not infer \
abilities the card does not have. Return ONLY the commander_profile tool call.\
"""


@dataclass
class CommanderProfileData:
    """In-memory commander profile used by the pool builder and evaluator."""
    oracle_id: str
    card_name: str

    provides_draw: bool = False
    provides_ramp: bool = False
    provides_tokens: bool = False
    provides_sac_outlet: bool = False
    provides_recursion: bool = False
    provides_graveyard_access: bool = False
    provides_exile_access: bool = False
    provides_cost_reduction: bool = False
    provides_removal: bool = False
    provides_protection: bool = False
    provides_wincon: bool = False
    provides_combo_piece: bool = False

    needs_creatures: bool = False
    needs_artifacts: bool = False
    needs_spells: bool = False
    needs_lands: bool = False
    needs_combat: bool = False
    needs_attack_damage_triggers: bool = False

    dependency_score: float = 0.0
    protection_need: float = 0.0
    recast_importance: float = 0.0

    preferred_archetypes: list[str] = field(default_factory=list)
    confidence: float = 0.8

    @property
    def provides_list(self) -> list[str]:
        """Human-readable list of what this commander provides."""
        flags = {
            "draw": self.provides_draw, "ramp": self.provides_ramp,
            "tokens": self.provides_tokens, "sac_outlet": self.provides_sac_outlet,
            "recursion": self.provides_recursion, "graveyard_access": self.provides_graveyard_access,
            "exile_access": self.provides_exile_access, "cost_reduction": self.provides_cost_reduction,
            "removal": self.provides_removal, "protection": self.provides_protection,
            "wincon": self.provides_wincon, "combo_piece": self.provides_combo_piece,
        }
        return [k for k, v in flags.items() if v]

    @property
    def needs_list(self) -> list[str]:
        flags = {
            "creatures": self.needs_creatures, "artifacts": self.needs_artifacts,
            "spells": self.needs_spells, "lands": self.needs_lands,
            "combat": self.needs_combat, "attack_damage_triggers": self.needs_attack_damage_triggers,
        }
        return [k for k, v in flags.items() if v]


def _row_to_profile(row: DBCommanderProfile) -> CommanderProfileData:
    return CommanderProfileData(
        oracle_id=row.oracle_id,
        card_name=row.card_name,
        provides_draw=row.provides_draw,
        provides_ramp=row.provides_ramp,
        provides_tokens=row.provides_tokens,
        provides_sac_outlet=row.provides_sac_outlet,
        provides_recursion=row.provides_recursion,
        provides_graveyard_access=row.provides_graveyard_access,
        provides_exile_access=row.provides_exile_access,
        provides_cost_reduction=row.provides_cost_reduction,
        provides_removal=row.provides_removal,
        provides_protection=row.provides_protection,
        provides_wincon=row.provides_wincon,
        provides_combo_piece=row.provides_combo_piece,
        needs_creatures=row.needs_creatures,
        needs_artifacts=row.needs_artifacts,
        needs_spells=row.needs_spells,
        needs_lands=row.needs_lands,
        needs_combat=row.needs_combat,
        needs_attack_damage_triggers=row.needs_attack_damage_triggers,
        dependency_score=float(row.dependency_score),
        protection_need=float(row.protection_need),
        recast_importance=float(row.recast_importance),
        preferred_archetypes=list(row.preferred_archetypes or []),
        confidence=float(row.confidence),
    )


def _call_llm(commander: Card) -> dict:
    """Single LLM call to generate profile JSON. Raises on failure."""
    oracle_text = commander.oracle_text or "(no oracle text)"
    user_msg = (
        f"Commander: {commander.name}\n"
        f"Mana cost: {commander.mana_cost or '—'}\n"
        f"Type: {commander.type_line}\n"
        f"CMC: {commander.cmc}\n"
        f"Oracle text:\n{oracle_text}"
    )

    client = anthropic.Anthropic(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_anthropic_base_url,
    )
    kwargs: dict = dict(
        model=settings.deepseek_model,
        max_tokens=512,
        system=_SYSTEM_PROMPT,
        tools=[_PROFILE_TOOL],
        tool_choice={"type": "tool", "name": "commander_profile"},
        messages=[{"role": "user", "content": user_msg}],
    )
    if "deepseek" in settings.deepseek_anthropic_base_url:
        kwargs["thinking"] = {"type": "disabled"}

    response = client.messages.create(**kwargs)
    tool_use = next(b for b in response.content if b.type == "tool_use")
    return tool_use.input


def _clamp(val: float, lo: float = 0.0, hi: float = 5.0) -> float:
    return max(lo, min(hi, float(val)))


def _save_profile(session: Session, commander: Card, inp: dict) -> DBCommanderProfile:
    """Upsert profile row from LLM output dict."""
    now = datetime.utcnow()
    existing = session.get(DBCommanderProfile, commander.oracle_id)

    if existing and existing.manual_override:
        return existing  # never overwrite manual entries

    row = existing or DBCommanderProfile(oracle_id=commander.oracle_id)
    row.card_name = commander.name
    row.provides_draw            = bool(inp.get("provides_draw"))
    row.provides_ramp            = bool(inp.get("provides_ramp"))
    row.provides_tokens          = bool(inp.get("provides_tokens"))
    row.provides_sac_outlet      = bool(inp.get("provides_sac_outlet"))
    row.provides_recursion       = bool(inp.get("provides_recursion"))
    row.provides_graveyard_access = bool(inp.get("provides_graveyard_access"))
    row.provides_exile_access    = bool(inp.get("provides_exile_access"))
    row.provides_cost_reduction  = bool(inp.get("provides_cost_reduction"))
    row.provides_removal         = bool(inp.get("provides_removal"))
    row.provides_protection      = bool(inp.get("provides_protection"))
    row.provides_wincon          = bool(inp.get("provides_wincon"))
    row.provides_combo_piece     = bool(inp.get("provides_combo_piece"))
    row.needs_creatures              = bool(inp.get("needs_creatures"))
    row.needs_artifacts              = bool(inp.get("needs_artifacts"))
    row.needs_spells                 = bool(inp.get("needs_spells"))
    row.needs_lands                  = bool(inp.get("needs_lands"))
    row.needs_combat                 = bool(inp.get("needs_combat"))
    row.needs_attack_damage_triggers = bool(inp.get("needs_attack_damage_triggers"))
    row.dependency_score  = _clamp(inp.get("dependency_score", 0))
    row.protection_need   = _clamp(inp.get("protection_need", 0))
    row.recast_importance = _clamp(inp.get("recast_importance", 0))
    row.preferred_archetypes = list(inp.get("preferred_archetypes", []))[:3]
    row.confidence    = max(0.0, min(1.0, float(inp.get("confidence", 0.8))))
    row.prompt_version = CURRENT_PROMPT_VERSION
    row.needs_review  = row.confidence < 0.6
    row.generated_at  = now
    row.raw_response  = inp

    if not existing:
        session.add(row)
    session.flush()
    return row


def get_or_generate_profile(
    session: Session,
    commander: Card,
    *,
    force_regenerate: bool = False,
) -> CommanderProfileData:
    """
    Return the cached profile for a commander, generating it via LLM if needed.
    Safe to call on every pool build — cached profiles are returned without an LLM call.
    """
    existing = session.get(DBCommanderProfile, commander.oracle_id)

    needs_generation = (
        existing is None
        or (existing.prompt_version != CURRENT_PROMPT_VERSION and not existing.manual_override)
        or force_regenerate
    )

    if needs_generation:
        try:
            inp = _call_llm(commander)
            row = _save_profile(session, commander, inp)
            return _row_to_profile(row)
        except Exception:
            # LLM failure — return a null profile so pool build still works
            return CommanderProfileData(
                oracle_id=commander.oracle_id,
                card_name=commander.name,
                confidence=0.0,
            )

    return _row_to_profile(existing)
