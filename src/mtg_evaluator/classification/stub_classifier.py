from mtg_evaluator.classification.base import BaseClassifier
from mtg_evaluator.classification.card_input import CardInput
from mtg_evaluator.classification.schema import (
    CardClassification,
    CardFunctionEnum,
    ZoneUsed,
    Timing,
    Resource,
    RolePowerScores,
    ArchetypeFitScores,
    BracketFitScores,
)


class StubClassifier(BaseClassifier):
    """Returns a structurally valid placeholder classification without an LLM."""

    @property
    def version(self) -> str:
        return "stub-1.0"

    def classify(self, card: CardInput) -> CardClassification:
        oracle_id = card.oracle_id
        card_name = card.name
        oracle_text = card.oracle_text
        type_line = card.type_line
        functions = self._infer_functions(oracle_text, type_line)
        zones = self._infer_zones(oracle_text, type_line)
        timing = self._infer_timing(type_line, oracle_text)

        return CardClassification(
            oracle_id=oracle_id,
            card_name=card_name,
            functions=functions,
            zones_used=zones,
            timing=timing,
            resources=[Resource.mana],
            general_commander_power=3,
            role_power=RolePowerScores(),
            archetype_fit=ArchetypeFitScores(midrange=3),
            bracket_fit=BracketFitScores(bracket_2=3, bracket_3=3),
            commander_context_notes=[],
            warnings=[],
        )

    def _infer_functions(self, oracle_text: str, type_line: str) -> list[CardFunctionEnum]:
        text = oracle_text.lower()
        functions: list[CardFunctionEnum] = []

        if "search your library" in text:
            functions.append(CardFunctionEnum.tutor)
        if "draw" in text and "card" in text:
            functions.append(CardFunctionEnum.draw)
        if "add" in text and "mana" in text:
            functions.append(CardFunctionEnum.ramp)
        if "destroy target" in text or "exile target" in text:
            if "creature" in text:
                functions.append(CardFunctionEnum.creature_removal)
            else:
                functions.append(CardFunctionEnum.removal)
        if "destroy all" in text or "exile all creatures" in text:
            functions.append(CardFunctionEnum.board_wipe)
        if "counter target" in text:
            functions.append(CardFunctionEnum.counterspell)
        if "create" in text and "token" in text:
            functions.append(CardFunctionEnum.token_maker)
        if "return" in text and "graveyard" in text:
            functions.append(CardFunctionEnum.recursion)
        if "put" in text and "graveyard" in text and "onto the battlefield" in text:
            functions.append(CardFunctionEnum.reanimation)
        if "sacrifice" in text and ":" in text:
            functions.append(CardFunctionEnum.sacrifice_outlet)

        if not functions:
            functions.append(CardFunctionEnum.mana_sink)

        return functions

    def _infer_zones(self, oracle_text: str, type_line: str) -> list[ZoneUsed]:
        text = oracle_text.lower()
        zones: list[ZoneUsed] = [ZoneUsed.battlefield]

        if "graveyard" in text:
            zones.append(ZoneUsed.graveyard)
        if "exile" in text:
            zones.append(ZoneUsed.exile)
        if "your hand" in text or "from hand" in text:
            zones.append(ZoneUsed.hand)
        if "library" in text:
            zones.append(ZoneUsed.library)
        if "stack" in text:
            zones.append(ZoneUsed.stack)

        return zones

    def _infer_timing(self, type_line: str, oracle_text: str) -> list[Timing]:
        tl = type_line.lower()
        text = oracle_text.lower()
        timing: list[Timing] = []

        if "instant" in tl:
            timing.append(Timing.instant_speed)
        elif "sorcery" in tl:
            timing.append(Timing.sorcery_speed)
        else:
            timing.append(Timing.sorcery_speed)

        if "whenever" in text or "when" in text:
            timing.append(Timing.triggered_ability)
        if "{" in text and "}: " in text:
            timing.append(Timing.activated_ability)

        return timing
