"""
Official Commander banlist (as of mid-2025).

Cards here are never legal in any bracket — they must be filtered from
candidate pools, browse results, and land tier tables. Game Changers are a
separate concept (bracket-gated, not banned); see evaluation/game_changers.py.
"""

from __future__ import annotations

BANNED_IN_COMMANDER: frozenset[str] = frozenset(
    {
        "Ancestral Recall",
        "Balance",
        "Biorhythm",
        "Black Lotus",
        "Channel",
        "Chaos Orb",
        "Coalition Victory",
        "Dockside Extortionist",
        "Emrakul, the Aeons Torn",
        "Erayo, Soratami Ascendant",
        "Falling Star",
        "Fastbond",
        "Flash",
        "Gifts Ungiven",
        "Golos, Tireless Pilgrim",
        "Griselbrand",
        "Hullbreacher",
        "Iona, Shield of Emeria",
        "Jeweled Lotus",
        "Karakas",
        "Leovold, Emissary of Trest",
        "Library of Alexandria",
        "Limited Resources",
        "Lutri, the Spellchaser",
        "Mana Crypt",
        "Mox Emerald",
        "Mox Jet",
        "Mox Pearl",
        "Mox Ruby",
        "Mox Sapphire",
        "Nadu, Winged Wisdom",
        "Panoptic Mirror",
        "Paradox Engine",
        "Primeval Titan",
        "Prophet of Kruphix",
        "Recurring Nightmare",
        "Rofellos, Llanowar Emissary",
        "Shahrazad",
        "Sundering Titan",
        "Sway of the Stars",
        "Sylvan Primordial",
        "Time Vault",
        "Time Walk",
        "Tinker",
        "Tolarian Academy",
        "Trade Secrets",
        "Upheaval",
        "Yawgmoth's Bargain",
    }
)


def is_banned(card_name: str) -> bool:
    return card_name.split(" // ")[0] in BANNED_IN_COMMANDER or card_name in BANNED_IN_COMMANDER
