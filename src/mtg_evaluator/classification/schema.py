from __future__ import annotations

import enum
from typing import Annotated, Optional

from pydantic import BaseModel, Field, field_validator

from mtg_evaluator.card_functions import normalize_functions


class CardFunctionEnum(str, enum.Enum):
    ramp = "ramp"
    draw = "draw"
    card_selection = "card_selection"
    removal = "removal"
    creature_removal = "creature_removal"
    artifact_removal = "artifact_removal"
    enchantment_removal = "enchantment_removal"
    board_wipe = "board_wipe"
    counterspell = "counterspell"
    tutor = "tutor"
    recursion = "recursion"
    reanimation = "reanimation"
    token_maker = "token_maker"
    sacrifice_outlet = "sacrifice_outlet"
    sacrifice_payoff = "sacrifice_payoff"
    aristocrat_payoff = "aristocrat_payoff"
    graveyard_hate = "graveyard_hate"
    graveyard_enabler = "graveyard_enabler"
    exile_enabler = "exile_enabler"
    exile_payoff = "exile_payoff"
    blink = "blink"
    etb_payoff = "etb_payoff"
    death_trigger_payoff = "death_trigger_payoff"
    protection = "protection"
    finisher = "finisher"
    anthem = "anthem"
    stax = "stax"
    combo_piece = "combo_piece"
    cost_reducer = "cost_reducer"
    mana_sink = "mana_sink"
    discard_outlet = "discard_outlet"
    impulse_draw = "impulse_draw"
    extra_combat = "extra_combat"
    extra_turn = "extra_turn"
    land_ramp = "land_ramp"
    treasure_maker = "treasure_maker"


class ZoneUsed(str, enum.Enum):
    battlefield = "battlefield"
    graveyard = "graveyard"
    exile = "exile"
    hand = "hand"
    library = "library"
    command_zone = "command_zone"
    stack = "stack"


class Timing(str, enum.Enum):
    instant_speed = "instant_speed"
    sorcery_speed = "sorcery_speed"
    activated_ability = "activated_ability"
    triggered_ability = "triggered_ability"
    static_ability = "static_ability"
    replacement_effect = "replacement_effect"


class Resource(str, enum.Enum):
    mana = "mana"
    cards = "cards"
    life = "life"
    creatures = "creatures"
    artifacts = "artifacts"
    enchantments = "enchantments"
    lands = "lands"
    graveyard = "graveyard"
    exile = "exile"
    library = "library"
    opponents = "opponents"


class ContextNote(str, enum.Enum):
    wants_high_creature_count = "wants_high_creature_count"
    wants_sacrifice_payoffs = "wants_sacrifice_payoffs"
    wants_graveyard_density = "wants_graveyard_density"
    better_with_cost_reduction = "better_with_cost_reduction"
    requires_commander_to_be_good = "requires_commander_to_be_good"
    wants_enchantment_density = "wants_enchantment_density"
    wants_artifact_density = "wants_artifact_density"
    wants_land_count = "wants_land_count"
    wants_spell_density = "wants_spell_density"
    wants_token_density = "wants_token_density"
    commander_synergy_dependent = "commander_synergy_dependent"
    better_in_multiplayer = "better_in_multiplayer"
    enables_infinite_mana = "enables_infinite_mana"
    part_of_two_card_combo = "part_of_two_card_combo"
    part_of_three_card_combo = "part_of_three_card_combo"


class Warning(str, enum.Enum):
    narrow_card = "narrow_card"
    trap_card = "trap_card"
    high_salt = "high_salt"
    commander_dependent = "commander_dependent"
    requires_density = "requires_density"
    mana_intensive = "mana_intensive"
    combo_enabler = "combo_enabler"
    pod_warping = "pod_warping"
    slow_without_ramp = "slow_without_ramp"
    weak_without_synergy = "weak_without_synergy"


Score = Annotated[int, Field(ge=1, le=5)]


class RolePowerScores(BaseModel):
    removal_power: Optional[Score] = None
    ramp_power: Optional[Score] = None
    draw_power: Optional[Score] = None
    tutor_power: Optional[Score] = None
    protection_power: Optional[Score] = None
    finisher_power: Optional[Score] = None
    stax_power: Optional[Score] = None
    combo_power: Optional[Score] = None
    recursion_power: Optional[Score] = None
    board_wipe_power: Optional[Score] = None


class ArchetypeFitScores(BaseModel):
    aristocrats: Optional[Score] = None
    reanimator: Optional[Score] = None
    blink: Optional[Score] = None
    tokens: Optional[Score] = None
    spellslinger: Optional[Score] = None
    voltron: Optional[Score] = None
    lands: Optional[Score] = None
    artifacts: Optional[Score] = None
    enchantress: Optional[Score] = None
    graveyard: Optional[Score] = None
    exile_matters: Optional[Score] = None
    combat: Optional[Score] = None
    control: Optional[Score] = None
    stax: Optional[Score] = None
    big_mana: Optional[Score] = None
    tribal: Optional[Score] = None
    equipment: Optional[Score] = None
    lifegain: Optional[Score] = None
    sacrifice: Optional[Score] = None
    treasure: Optional[Score] = None
    pillow_fort: Optional[Score] = None
    group_slug: Optional[Score] = None
    group_hug: Optional[Score] = None
    toolbox: Optional[Score] = None
    midrange: Optional[Score] = None


class BracketFitScores(BaseModel):
    casual: Optional[Score] = None
    bracket_2: Optional[Score] = None
    bracket_3: Optional[Score] = None
    bracket_4: Optional[Score] = None
    cedh: Optional[Score] = None


class CardClassification(BaseModel):
    oracle_id: str
    card_name: str
    functions: list[CardFunctionEnum]
    zones_used: list[ZoneUsed]
    timing: list[Timing]
    resources: list[Resource]
    general_commander_power: Score
    role_power: RolePowerScores
    archetype_fit: ArchetypeFitScores
    bracket_fit: BracketFitScores
    commander_context_notes: list[ContextNote]
    warnings: list[Warning]

    model_config = {"use_enum_values": True}

    @field_validator("functions", mode="before")
    @classmethod
    def _normalize_functions(cls, value):
        return normalize_functions(value)
