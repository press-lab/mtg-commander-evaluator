"""
Land quality scoring for Commander pool building.

Lands are scored on EDH utility — mana consistency, enters untapped,
and special abilities — rather than archetype fit (which is meaningless
for most lands). The score (0–100) overrides the composite archetype score
in the pool builder so that dual lands bubble above basics regardless of
LLM classification coverage.

Tiers:
  T1 (90-95) — Original duals, fetchlands, premier utility lands
  T2 (75-85) — Shocklands, horizon lands, Mana Confluence, City of Brass,
                Commander staples (Command Tower, Exotic Orchard)
  T3 (60-72) — Check lands, pain lands, fast lands, filter lands,
                Pathway lands, bond lands (Battlebond)
  T4 (45-58) — Reveal/shadow lands, tango/battle lands, survivor lands,
                slow fetches (Fabled Passage), tri-lands
  T5 (25-40) — Bounce lands, gain lands, tap duals, cycling lands
  Basic (15) — Plains/Island/Swamp/Mountain/Forest/Wastes

Bracket modifier: Ancient Tomb and similar high-power utility lands are
only included in T1 at B3+; at B1/B2 they score as T3. The pool builder
applies a bracket_penalty on flagged cards.

Cards NOT in this table fall through to the composite score (LLM-classified).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# T1 — Enters untapped, two (or any) colors, or premier utility
# ---------------------------------------------------------------------------
_T1: list[str] = [
    # Original dual lands
    "Underground Sea", "Tundra", "Volcanic Island", "Badlands", "Taiga",
    "Savannah", "Scrubland", "Bayou", "Tropical Island", "Plateau",

    # On-color fetchlands (all 10)
    "Polluted Delta", "Flooded Strand", "Bloodstained Mire", "Wooded Foothills",
    "Windswept Heath", "Scalding Tarn", "Verdant Catacombs", "Arid Mesa",
    "Misty Rainforest", "Marsh Flats",

    # Commander staple
    "Command Tower",

    # Any-color utility
    "Mana Confluence", "City of Brass", "Reflecting Pool",

    # High-power utility (bracket-gated in assembler prompt, not here)
    "Ancient Tomb", "Gaea's Cradle", "Serra's Sanctum",
    "The Tabernacle at Pendrell Vale", "Mishra's Workshop",
    "Library of Alexandria", "Tolarian Academy",

    # Cabal Coffers (good in mono-black / heavy black)
    "Cabal Coffers",

    # Strip Mine / Wasteland — disruption lands
    "Strip Mine", "Wasteland",
]

# ---------------------------------------------------------------------------
# T2 — Strong dual, enters untapped with minor condition / life payment
# ---------------------------------------------------------------------------
_T2: list[str] = [
    # Shocklands
    "Watery Grave", "Hallowed Fountain", "Blood Crypt", "Stomping Ground",
    "Temple Garden", "Godless Shrine", "Steam Vents", "Overgrown Tomb",
    "Sacred Foundry", "Breeding Pool",

    # Horizon lands (draw a card when sacrificed)
    "Horizon Canopy", "Fiery Islet", "Sunbaked Canyon", "Nurturing Peatland",
    "Silent Clearing", "Waterlogged Grove",

    # Near-Commander-staple utility
    "Exotic Orchard", "Forbidden Orchard",
    "Urborg, Tomb of Yawgmoth",     # pairs with Cabal Coffers
    "Cavern of Souls",

    # Utility duals / tri-color
    "Gemstone Mine", "Gemstone Caverns",
    "Spire of Industry",

    # Pain lands
    "Underground River", "Adarkar Wastes", "Shivan Reef", "Sulfurous Springs",
    "Karplusan Forest", "Brushland", "Caves of Koilos", "Battlefield Forge",
    "Llanowar Wastes", "Yavimaya Coast",

    # Original ABU pain lands (same cards, listed for alternate printings)
    # already covered above

    # Fetches (off-color but still fetchable)
    "Prismatic Vista",
]

# ---------------------------------------------------------------------------
# T3 — Good duals with small downsides, or niche utility worth including
# ---------------------------------------------------------------------------
_T3: list[str] = [
    # Check lands (enters untapped if you control basic of right type)
    "Drowned Catacomb", "Glacial Fortress", "Sulfur Falls", "Clifftop Retreat",
    "Hinterland Harbor", "Isolated Chapel", "Woodland Cemetery", "Sunpetal Grove",
    "Rootbound Crag", "Dragonskull Summit",

    # Fast lands (untapped if ≤2 other lands)
    "Darkslick Shores", "Seachrome Coast", "Blackcleave Cliffs", "Copperline Gorge",
    "Razorverge Thicket", "Concealed Courtyard", "Inspiring Vantage",
    "Spirebluff Canal", "Blooming Marsh", "Botanical Sanctum",

    # Filter lands
    "Flooded Grove", "Sunken Ruins", "Twilight Mire", "Fire-Lit Thicket",
    "Wooded Bastion", "Fetid Heath", "Rugged Prairie", "Cascade Bluffs",
    "Graven Cairns", "Mystic Gate",

    # Pathway lands (single color but untapped)
    "Clearwater Pathway", "Brightclimb Pathway", "Blightstep Pathway",
    "Cragcrown Pathway", "Darkbore Pathway", "Needleverge Pathway",
    "Riverglide Pathway", "Searoad Pathway", "Branchloft Pathway",
    "Emeria's Call",  # DFC land/spell

    # Bond / Battlebond lands (untapped if 2+ opponents — always in Commander)
    "Sea of Clouds", "Morphic Pool", "Luxury Suite", "Spire Garden",
    "Bountiful Promenade", "Vault of Champions", "Training Center",
    "Spectator Seating", "Rejuvenating Springs", "Undergrowth Stadium",

    # Fetch-adjacent slow fetches
    "Fabled Passage",

    # Utility lands (broadly useful)
    "Reliquary Tower", "War Room",
    "Maze of Ith", "Glacial Chasm",         # GC — bracket-gated by assembler
    "Kor Haven",
    "Bojuka Bog",                            # graveyard hate on ETB
    "Rogue's Passage", "Emergence Zone",
    "Field of the Dead",                     # GC — bracket-gated
    "Nykthos, Shrine to Nyx",
    "Cradle of the Accursed",               # niche
    "Vault of the Archangel",
    "Kessig Wolf Run",
    "Desolate Lighthouse",
    "Homeward Path",
    "Yavimaya, Cradle of Growth",

    # Commander-specific utility
    "Command Beacon",
    "Plaza of Heroes",
    "Eiganjo, Seat of the Empire",          # channel lands
    "Otawara, Soaring City",
    "Takenuma, Abandoned Mire",
    "Sokenzan, Crucible of Defiance",
    "Boseiju, Who Endures",

    # Tri-color utility
    "Mana Confluence",  # already T1 — skip
    "Forbidden Orchard",  # T2
]

# ---------------------------------------------------------------------------
# T4 — Enters tapped under some conditions, or niche single-color utility
# ---------------------------------------------------------------------------
_T4: list[str] = [
    # Reveal / shadow lands (untapped if you reveal right basic)
    "Port Town", "Choked Estuary", "Foreboding Ruins", "Fortified Village",
    "Game Trail", "Shineshadow Snarl", "Frostboil Snarl", "Furycalm Snarl",
    "Necroblossom Snarl", "Vineglimmer Snarl",

    # Tango / Battle lands (untapped if 2+ basics — harder in 3-color decks)
    "Prairie Stream", "Sunken Hollow", "Smoldering Marsh", "Cinder Glade",
    "Canopy Vista",

    # Survivor / creature-check lands
    "Undergrowth Stadium",  # listed in T3 — already covered

    # Bounce lands (ETB tap two, return one) — card advantage vs tempo loss
    "Dimir Aqueduct", "Azorius Chancery", "Rakdos Carnarium", "Gruul Turf",
    "Selesnya Sanctuary", "Orzhov Basilica", "Izzet Boilerworks", "Golgari Rot Farm",
    "Simic Growth Chamber", "Boros Garrison",

    # Tri-color tap lands
    "Arcane Sanctum", "Crumbling Necropolis", "Jungle Shrine", "Savage Lands",
    "Seaside Citadel", "Mystic Monastery", "Nomad Outpost", "Opulent Palace",
    "Sandsteppe Citadel", "Frontier Bivouac",
    # 4-color tap
    "Arid Mesa",  # already fetched — skip; these are the Khans tri-lands above

    # Cycling lands (emergency cycle, enters tapped)
    "Ash Barrens",  # basic-cycle — borderline T3 in landfall/lands decks
    "Irrigated Farmland", "Fetid Pools", "Sheltered Thicket", "Canyon Slough",
    "Scattered Groves",

    # Utility (enters tapped)
    "Buried Ruin",
    "Gavony Township",
    "Moorland Haunt",
    "Nephalia Drownyard",
    "Stensia Bloodhall",
    "Skarrg, the Rage Pits",
    "Grim Backwoods",
    "Shizo, Death's Storehouse",
    "Minamo, School at Water's Edge",
    "Okina, Temple to the Grandfathers",
    "Eiganjo Castle",
    "Kor Haven",   # already T3

    # Evolving Wilds / Terramorphic Expanse (fetch basics, enters tapped)
    "Evolving Wilds", "Terramorphic Expanse",
    # Off-color fetches (still fix mana, thin the deck)
    "Naya Panorama", "Bant Panorama", "Esper Panorama",
    "Jund Panorama", "Grixis Panorama",
]

# ---------------------------------------------------------------------------
# T5 — Enters tapped, minimal upside
# ---------------------------------------------------------------------------
_T5: list[str] = [
    # Gain lands (Khans, Zendikar)
    "Tranquil Cove", "Dismal Backwater", "Bloodfell Caves", "Rugged Highlands",
    "Blossoming Sands", "Scoured Barrens", "Jungle Hollow", "Wind-Scarred Crag",
    "Thornwood Falls", "Swiftwater Cliffs",
    # More gain lands
    "Sejiri Refuge", "Jwar Isle Refuge", "Graypelt Refuge", "Kazandu Refuge",
    "Akoum Refuge", "Kabira Crossroads",
    # Ravnica tap duals
    "Dimir Guildgate", "Azorius Guildgate", "Rakdos Guildgate", "Gruul Guildgate",
    "Selesnya Guildgate", "Orzhov Guildgate", "Izzet Guildgate", "Golgari Guildgate",
    "Simic Guildgate", "Boros Guildgate",
    # Snarls (already T4 if you have basics, T5 without)
    # Plains/tap duals in Innistrad block
    "Thriving Isle", "Thriving Moor", "Thriving Bluff", "Thriving Grove", "Thriving Heath",
]

# ---------------------------------------------------------------------------
# Basic lands — always available, score low because non-basics are preferred
# ---------------------------------------------------------------------------
_BASICS: list[str] = [
    "Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes",
    "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
    "Snow-Covered Mountain", "Snow-Covered Forest",
]

# ---------------------------------------------------------------------------
# Score table (name → 0-100 score)
# ---------------------------------------------------------------------------
def _build_table() -> dict[str, float]:
    table: dict[str, float] = {}
    for name in _T1:
        table[name] = 92.0
    for name in _T2:
        if name not in table:
            table[name] = 78.0
    for name in _T3:
        if name not in table:
            table[name] = 65.0
    for name in _T4:
        if name not in table:
            table[name] = 50.0
    for name in _T5:
        if name not in table:
            table[name] = 30.0
    for name in _BASICS:
        if name not in table:
            table[name] = 15.0
    return table


LAND_SCORES: dict[str, float] = _build_table()


# Cards that are only high-power appropriate — reduce score at B1/B2
_BRACKET_RESTRICTED: dict[str, int] = {
    # name → minimum bracket to get full T1 score (below this → T3 score)
    "Ancient Tomb": 3,
    "Gaea's Cradle": 3,
    "Serra's Sanctum": 3,
    "The Tabernacle at Pendrell Vale": 4,
    "Mishra's Workshop": 4,
    "Library of Alexandria": 4,
    "Tolarian Academy": 4,
    "Strip Mine": 3,
    "Wasteland": 3,
    "Glacial Chasm": 3,
    "Field of the Dead": 3,
}


def land_score(card_name: str, bracket: int) -> float | None:
    """
    Return quality score for a land (0-100), or None if the land is not
    in our table (let it fall through to the composite score).

    Applies bracket penalty for high-power lands at casual brackets.
    """
    score = LAND_SCORES.get(card_name)
    if score is None:
        return None
    min_bracket = _BRACKET_RESTRICTED.get(card_name)
    if min_bracket and bracket < min_bracket:
        score = min(score, 58.0)   # clamp to T4 range
    return score
