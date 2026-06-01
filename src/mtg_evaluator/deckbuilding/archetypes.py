"""
Canonical archetype names and synonym mapping.

Canonical archetypes are the exact strings used in card_archetype_scores.archetype.
Synonyms map common player vocabulary → canonical name so a user typing "sac" or
"wheels" or "go wide" gets the right archetype scores pulled from the DB.

If a user input doesn't match any synonym or canonical name we pass it through
unchanged — the DB join will return no scores and the pool falls back to
bracket/role/popularity ranking.
"""

from __future__ import annotations

# Canonical archetypes that exist in card_archetype_scores (as of June 2026 classification run).
# Ordered roughly by card count in DB.
CANONICAL_ARCHETYPES: list[str] = [
    "midrange",
    "control",
    "combat",
    "tokens",
    "tribal",
    "aristocrats",
    "toolbox",
    "big_mana",
    "sacrifice",
    "spellslinger",
    "voltron",
    "reanimator",
    "graveyard",
    "artifacts",
    "blink",
    "enchantress",
    "equipment",
    "treasure",
    "lifegain",
    "lands",
    "group_slug",
    "stax",
    "pillow_fort",
    "exile_matters",
    "group_hug",
]

# Synonym → canonical archetype.
# Keys are lowercased; normalization lowercases + strips before lookup.
_SYNONYMS: dict[str, str] = {
    # Sacrifice / Aristocrats
    "sac": "aristocrats",
    "sacrifice": "aristocrats",  # "aristocrats" has more cards in DB
    "blood artist": "aristocrats",
    "death triggers": "aristocrats",
    "free sac": "sacrifice",
    "free sacrifice": "sacrifice",
    # Tokens / Go Wide
    "go wide": "tokens",
    "token": "tokens",
    "populate": "tokens",
    "wide": "tokens",
    # Combat / Aggro
    "aggro": "combat",
    "attack": "combat",
    "combat tricks": "combat",
    "go tall": "voltron",
    "infect": "voltron",
    "poison": "voltron",
    "aura": "voltron",
    "auras": "voltron",
    "commander damage": "voltron",
    "21 damage": "voltron",
    # Spellslinger / Storm
    "spells": "spellslinger",
    "instant": "spellslinger",
    "instants": "spellslinger",
    "sorcery": "spellslinger",
    "sorceries": "spellslinger",
    "storm": "spellslinger",
    "wheels": "spellslinger",
    "wheel": "spellslinger",
    "cantrips": "spellslinger",
    "prowess": "spellslinger",
    "magecraft": "spellslinger",
    # Graveyard / Reanimator
    "reanimation": "reanimator",
    "reanimate": "reanimator",
    "cheat": "reanimator",
    "cheat into play": "reanimator",
    "self mill": "graveyard",
    "self-mill": "graveyard",
    "dredge": "graveyard",
    "recursion": "graveyard",
    "grave": "graveyard",
    "yard": "graveyard",
    "flashback": "graveyard",
    "delve": "graveyard",
    # Ramp / Big Mana
    "ramp": "big_mana",
    "big spells": "big_mana",
    "green ramp": "big_mana",
    "x spells": "big_mana",
    "eldrazi": "big_mana",
    # Artifacts
    "artifact": "artifacts",
    "affinity": "artifacts",
    "thopter": "artifacts",
    "eggs": "artifacts",
    "vehicles": "artifacts",
    "constructs": "artifacts",
    # Enchantments
    "enchantment": "enchantress",
    "enchantments": "enchantress",
    "aura package": "enchantress",
    "constellation": "enchantress",
    "sagas": "enchantress",
    # Equipment
    "equip": "equipment",
    "swords": "equipment",
    "living weapon": "equipment",
    # Tribal
    "tribe": "tribal",
    "lord": "tribal",
    "lords": "tribal",
    "elf": "tribal",
    "elves": "tribal",
    "goblin": "tribal",
    "goblins": "tribal",
    "zombie": "tribal",
    "zombies": "tribal",
    "dragon": "tribal",
    "dragons": "tribal",
    "vampire": "tribal",
    "vampires": "tribal",
    "merfolk": "tribal",
    "soldier": "tribal",
    "soldiers": "tribal",
    "wizard": "tribal",
    "wizards": "tribal",
    "knight": "tribal",
    "knights": "tribal",
    "human": "tribal",
    "humans": "tribal",
    "sliver": "tribal",
    "slivers": "tribal",
    "cat": "tribal",
    "cats": "tribal",
    "bird": "tribal",
    "birds": "tribal",
    # Blink / Flicker
    "flicker": "blink",
    "bounce": "blink",
    "etb": "blink",
    "enters the battlefield": "blink",
    "enter the battlefield": "blink",
    # Lifegain
    "life": "lifegain",
    "life gain": "lifegain",
    "life total": "lifegain",
    "extort": "lifegain",
    "soul sisters": "lifegain",
    # Stax / Hatebears
    "hatebears": "stax",
    "bears": "stax",
    "hate": "stax",
    "prison": "stax",
    "tax": "stax",
    "taxes": "stax",
    "winter orb": "stax",
    "soft lock": "stax",
    # Control
    "counterspells": "control",
    "counter": "control",
    "counters spells": "control",
    "permission": "control",
    "tempo": "control",
    "superfriends": "control",
    "planeswalkers": "control",
    # Pillow Fort
    "pillowfort": "pillow_fort",
    "pillows": "pillow_fort",
    "fort": "pillow_fort",
    "fog": "pillow_fort",
    "moat": "pillow_fort",
    # Lands / Landfall
    "landfall": "lands",
    "land matters": "lands",
    "lands matter": "lands",
    "land destruction": "lands",
    "land": "lands",
    "loam": "lands",
    "crucible": "lands",
    # Treasure
    "treasures": "treasure",
    "gold": "treasure",
    "clues": "treasure",
    "food": "treasure",
    "myr": "treasure",
    # Group Slug
    "slug": "group_slug",
    "burn everyone": "group_slug",
    "damage everyone": "group_slug",
    "symmetric damage": "group_slug",
    "wildfire": "group_slug",
    # Group Hug
    "hug": "group_hug",
    "politics": "group_hug",
    "share": "group_hug",
    # Exile matters
    "exile": "exile_matters",
    "suspend": "exile_matters",
    "imprint": "exile_matters",
    # Toolbox / Good Stuff
    "toolbox": "toolbox",
    "goodstuff": "midrange",
    "good stuff": "midrange",
    "good-stuff": "midrange",
    "value": "midrange",
    "good cards": "midrange",
    "battlecruiser": "midrange",
    "combo": "toolbox",
}


def normalize_archetype(user_input: str | None) -> str | None:
    """
    Map user-typed archetype to the canonical DB archetype name.
    Returns None if input is None/empty. Returns the canonical name if matched,
    otherwise returns the original (lowercased + stripped) so the DB join
    can still attempt a direct match.
    """
    if not user_input:
        return None
    normalized = user_input.lower().strip().replace("-", "_")
    # Direct canonical match (handles underscored names like "big_mana")
    if normalized in CANONICAL_ARCHETYPES:
        return normalized
    # Try synonym lookup (try both raw and underscored)
    result = _SYNONYMS.get(normalized) or _SYNONYMS.get(normalized.replace("_", " "))
    if result:
        return result
    # Return as-is — maybe the user typed a valid archetype we haven't seen yet
    return normalized
