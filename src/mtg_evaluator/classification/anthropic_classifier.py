import json

import anthropic

from mtg_evaluator.classification.base import BaseClassifier
from mtg_evaluator.classification.card_input import CardInput
from mtg_evaluator.classification.prompt import SYSTEM_PROMPT, build_user_prompt
from mtg_evaluator.classification.schema import CardClassification
from mtg_evaluator.classification.validator import validate_classification
from mtg_evaluator.config import settings

# Tool schema mirrors CardClassification — Anthropic tool use guarantees structured output.
_TOOL_SCHEMA = {
    "name": "classify_card",
    "description": "Classify a Magic: The Gathering card for Commander play.",
    "input_schema": {
        "type": "object",
        "required": [
            "oracle_id",
            "card_name",
            "functions",
            "zones_used",
            "timing",
            "resources",
            "general_commander_power",
            "role_power",
            "archetype_fit",
            "bracket_fit",
            "commander_context_notes",
            "warnings",
        ],
        "properties": {
            "oracle_id": {"type": "string"},
            "card_name": {"type": "string"},
            "functions": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "ramp",
                        "draw",
                        "card_selection",
                        "removal",
                        "creature_removal",
                        "artifact_removal",
                        "enchantment_removal",
                        "board_wipe",
                        "counterspell",
                        "tutor",
                        "recursion",
                        "reanimation",
                        "token_maker",
                        "sacrifice_outlet",
                        "sacrifice_payoff",
                        "aristocrat_payoff",
                        "graveyard_hate",
                        "graveyard_enabler",
                        "exile_enabler",
                        "exile_payoff",
                        "blink",
                        "etb_payoff",
                        "death_trigger_payoff",
                        "protection",
                        "finisher",
                        "anthem",
                        "stax",
                        "combo_piece",
                        "cost_reducer",
                        "mana_sink",
                        "discard_outlet",
                        "impulse_draw",
                        "extra_combat",
                        "extra_turn",
                        "land_ramp",
                        "treasure_maker",
                    ],
                },
            },
            "zones_used": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "battlefield",
                        "graveyard",
                        "exile",
                        "hand",
                        "library",
                        "command_zone",
                        "stack",
                    ],
                },
            },
            "timing": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "instant_speed",
                        "sorcery_speed",
                        "activated_ability",
                        "triggered_ability",
                        "static_ability",
                        "replacement_effect",
                    ],
                },
            },
            "resources": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "mana",
                        "cards",
                        "life",
                        "creatures",
                        "artifacts",
                        "enchantments",
                        "lands",
                        "graveyard",
                        "exile",
                        "library",
                        "opponents",
                    ],
                },
            },
            "general_commander_power": {"type": "integer", "minimum": 1, "maximum": 5},
            "role_power": {
                "type": "object",
                "properties": {
                    k: {"type": ["integer", "null"], "minimum": 1, "maximum": 5}
                    for k in [
                        "removal_power",
                        "ramp_power",
                        "draw_power",
                        "tutor_power",
                        "protection_power",
                        "finisher_power",
                        "stax_power",
                        "combo_power",
                        "recursion_power",
                        "board_wipe_power",
                    ]
                },
            },
            "archetype_fit": {
                "type": "object",
                "properties": {
                    k: {"type": ["integer", "null"], "minimum": 1, "maximum": 5}
                    for k in [
                        "aristocrats",
                        "reanimator",
                        "blink",
                        "tokens",
                        "spellslinger",
                        "voltron",
                        "lands",
                        "artifacts",
                        "enchantress",
                        "graveyard",
                        "exile_matters",
                        "combat",
                        "control",
                        "stax",
                        "big_mana",
                        "tribal",
                        "equipment",
                        "lifegain",
                        "sacrifice",
                        "treasure",
                        "pillow_fort",
                        "group_slug",
                        "group_hug",
                        "toolbox",
                        "midrange",
                    ]
                },
            },
            "bracket_fit": {
                "type": "object",
                "properties": {
                    k: {"type": ["integer", "null"], "minimum": 1, "maximum": 5}
                    for k in ["casual", "bracket_2", "bracket_3", "bracket_4", "cedh"]
                },
            },
            "commander_context_notes": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "wants_high_creature_count",
                        "wants_sacrifice_payoffs",
                        "wants_graveyard_density",
                        "better_with_cost_reduction",
                        "requires_commander_to_be_good",
                        "wants_enchantment_density",
                        "wants_artifact_density",
                        "wants_land_count",
                        "wants_spell_density",
                        "wants_token_density",
                        "commander_synergy_dependent",
                        "better_in_multiplayer",
                        "enables_infinite_mana",
                        "part_of_two_card_combo",
                        "part_of_three_card_combo",
                    ],
                },
            },
            "warnings": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "narrow_card",
                        "trap_card",
                        "high_salt",
                        "commander_dependent",
                        "requires_density",
                        "mana_intensive",
                        "combo_enabler",
                        "pod_warping",
                        "slow_without_ramp",
                        "weak_without_synergy",
                    ],
                },
            },
        },
    },
}


class AnthropicClassifier(BaseClassifier):
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._base_url = base_url
        self._client = anthropic.Anthropic(
            api_key=api_key or settings.anthropic_api_key,
            base_url=base_url,
        )
        self._model = model or settings.anthropic_model

    @property
    def version(self) -> str:
        return f"anthropic/{self._model}"

    def classify(self, card: CardInput) -> CardClassification:
        user_prompt = build_user_prompt(card)

        kwargs = dict(
            model=self._model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "classify_card"},
            messages=[{"role": "user", "content": user_prompt}],
        )
        # DeepSeek thinking mode is incompatible with tool_choice — disable it
        if self._base_url and "deepseek" in self._base_url:
            kwargs["thinking"] = {"type": "disabled"}
        response = self._client.messages.create(**kwargs)

        tool_use_block = next(
            (block for block in response.content if block.type == "tool_use"),
            None,
        )
        if tool_use_block is None:
            raise ValueError(
                f"Anthropic did not return a tool_use block for {card.name}"
            )

        raw_dict: dict = tool_use_block.input
        raw_dict["oracle_id"] = card.oracle_id
        raw_dict["card_name"] = card.name

        is_valid, errors, classification = validate_classification(raw_dict)
        if not is_valid:
            raise ValueError(
                f"Classification failed validation for {card.name}: {errors}"
            )

        return classification
