import pytest
from pydantic import ValidationError

from mtg_evaluator.classification.card_input import CardInput
from mtg_evaluator.classification.schema import (
    CardClassification,
    CardFunctionEnum,
    ZoneUsed,
    Timing,
    Resource,
    ContextNote,
    Warning,
    RolePowerScores,
    ArchetypeFitScores,
    BracketFitScores,
)
from mtg_evaluator.classification.validator import validate_classification
from mtg_evaluator.classification.stub_classifier import StubClassifier

VALID_CLASSIFICATION = {
    "oracle_id": "a2daf943-dc88-4c8b-ac97-4476ea6abb9c",
    "card_name": "Swords to Plowshares",
    "functions": ["creature_removal", "removal"],
    "zones_used": ["battlefield", "exile"],
    "timing": ["instant_speed"],
    "resources": ["mana"],
    "general_commander_power": 5,
    "role_power": {"removal_power": 5},
    "archetype_fit": {"control": 4, "toolbox": 5},
    "bracket_fit": {
        "casual": 3,
        "bracket_2": 4,
        "bracket_3": 5,
        "bracket_4": 5,
        "cedh": 5,
    },
    "commander_context_notes": [],
    "warnings": [],
}


class TestValidClassification:
    def test_valid_full_classification_passes(self):
        classification = CardClassification.model_validate(VALID_CLASSIFICATION)
        assert classification.card_name == "Swords to Plowshares"
        assert classification.general_commander_power == 5

    def test_empty_functions_list_is_valid(self):
        data = {**VALID_CLASSIFICATION, "functions": []}
        classification = CardClassification.model_validate(data)
        assert classification.functions == []

    def test_empty_warnings_is_valid(self):
        data = {**VALID_CLASSIFICATION, "warnings": []}
        classification = CardClassification.model_validate(data)
        assert classification.warnings == []

    def test_all_optional_role_power_fields_can_be_none(self):
        data = {**VALID_CLASSIFICATION, "role_power": {}}
        classification = CardClassification.model_validate(data)
        assert classification.role_power.removal_power is None

    def test_all_optional_archetype_scores_can_be_none(self):
        data = {**VALID_CLASSIFICATION, "archetype_fit": {}}
        classification = CardClassification.model_validate(data)
        assert classification.archetype_fit.aristocrats is None


class TestScoreValidation:
    def test_score_of_zero_fails(self):
        data = {**VALID_CLASSIFICATION, "general_commander_power": 0}
        with pytest.raises(ValidationError):
            CardClassification.model_validate(data)

    def test_score_of_six_fails(self):
        data = {**VALID_CLASSIFICATION, "general_commander_power": 6}
        with pytest.raises(ValidationError):
            CardClassification.model_validate(data)

    def test_score_of_one_passes(self):
        data = {**VALID_CLASSIFICATION, "general_commander_power": 1}
        classification = CardClassification.model_validate(data)
        assert classification.general_commander_power == 1

    def test_score_of_five_passes(self):
        data = {**VALID_CLASSIFICATION, "general_commander_power": 5}
        classification = CardClassification.model_validate(data)
        assert classification.general_commander_power == 5

    def test_archetype_score_out_of_range_fails(self):
        data = {**VALID_CLASSIFICATION, "archetype_fit": {"control": 6}}
        with pytest.raises(ValidationError):
            CardClassification.model_validate(data)

    def test_role_power_score_zero_fails(self):
        data = {**VALID_CLASSIFICATION, "role_power": {"removal_power": 0}}
        with pytest.raises(ValidationError):
            CardClassification.model_validate(data)


class TestEnumValidation:
    def test_unknown_function_enum_fails(self):
        data = {**VALID_CLASSIFICATION, "functions": ["not_a_real_function"]}
        with pytest.raises(ValidationError):
            CardClassification.model_validate(data)

    def test_unknown_zone_fails(self):
        data = {**VALID_CLASSIFICATION, "zones_used": ["the_moon"]}
        with pytest.raises(ValidationError):
            CardClassification.model_validate(data)

    def test_unknown_timing_fails(self):
        data = {**VALID_CLASSIFICATION, "timing": ["whenever_you_want"]}
        with pytest.raises(ValidationError):
            CardClassification.model_validate(data)

    def test_unknown_context_note_fails(self):
        data = {
            **VALID_CLASSIFICATION,
            "commander_context_notes": ["vague_prose_comment"],
        }
        with pytest.raises(ValidationError):
            CardClassification.model_validate(data)

    def test_unknown_warning_fails(self):
        data = {**VALID_CLASSIFICATION, "warnings": ["probably_fine"]}
        with pytest.raises(ValidationError):
            CardClassification.model_validate(data)


class TestValidateClassificationFunction:
    def test_valid_dict_returns_true(self):
        is_valid, errors, classification = validate_classification(VALID_CLASSIFICATION)
        assert is_valid is True
        assert errors == []
        assert classification is not None

    def test_invalid_dict_returns_false(self):
        bad = {**VALID_CLASSIFICATION, "general_commander_power": 99}
        is_valid, errors, classification = validate_classification(bad)
        assert is_valid is False
        assert len(errors) > 0
        assert classification is None

    def test_missing_required_field_returns_errors(self):
        bad = {k: v for k, v in VALID_CLASSIFICATION.items() if k != "card_name"}
        is_valid, errors, _ = validate_classification(bad)
        assert is_valid is False
        assert any("card_name" in e for e in errors)


class TestStubClassifier:
    def setup_method(self):
        self.classifier = StubClassifier()

    def test_stub_version(self):
        assert self.classifier.version == "stub-1.0"

    def _make_card(self, oracle_id, name, oracle_text, type_line):
        return CardInput(
            oracle_id=oracle_id, name=name, oracle_text=oracle_text, type_line=type_line
        )

    def test_stub_output_passes_validation(self):
        card = self._make_card(
            "a2daf943-dc88-4c8b-ac97-4476ea6abb9c",
            "Swords to Plowshares",
            "Exile target creature. Its controller gains life equal to its power.",
            "Instant",
        )
        result = self.classifier.classify(card)
        assert isinstance(result, CardClassification)
        is_valid, errors, _ = validate_classification(result.model_dump())
        assert is_valid is True, f"Stub output failed validation: {errors}"

    def test_stub_infers_draw_from_oracle_text(self):
        result = self.classifier.classify(
            self._make_card("test-draw", "Divination", "Draw two cards.", "Sorcery")
        )
        assert "draw" in result.functions

    def test_stub_infers_tutor(self):
        result = self.classifier.classify(
            self._make_card(
                "test-tutor",
                "Cultivate",
                "Search your library for up to two basic land cards.",
                "Sorcery",
            )
        )
        assert "tutor" in result.functions

    def test_stub_infers_counterspell(self):
        result = self.classifier.classify(
            self._make_card(
                "test-counter", "Counterspell", "Counter target spell.", "Instant"
            )
        )
        assert "counterspell" in result.functions

    def test_stub_infers_instant_timing(self):
        result = self.classifier.classify(
            self._make_card(
                "test-instant",
                "Swords to Plowshares",
                "Exile target creature.",
                "Instant",
            )
        )
        assert "instant_speed" in result.timing

    def test_stub_general_power_in_range(self):
        result = self.classifier.classify(
            self._make_card("test-power", "Forest", "", "Basic Land — Forest")
        )
        assert 1 <= result.general_commander_power <= 5
