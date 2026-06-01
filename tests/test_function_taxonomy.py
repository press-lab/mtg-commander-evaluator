from mtg_evaluator.card_functions import (
    function_group,
    has_function,
    normalize_function,
    normalize_functions,
    role_bucket,
)
from mtg_evaluator.classification.schema import CardClassification
from mtg_evaluator.deckbuilding.packages import check_package_health

VALID_CLASSIFICATION = {
    "oracle_id": "a2daf943-dc88-4c8b-ac97-4476ea6abb9c",
    "card_name": "Test Card",
    "functions": [],
    "zones_used": ["battlefield"],
    "timing": ["sorcery_speed"],
    "resources": ["mana"],
    "general_commander_power": 3,
    "role_power": {},
    "archetype_fit": {},
    "bracket_fit": {},
    "commander_context_notes": [],
    "warnings": [],
}


def test_normalize_function_aliases():
    assert normalize_function("sac_outlet") == "sacrifice_outlet"
    assert normalize_function("sacrifice") == "sacrifice_outlet"
    assert normalize_function("tokens") == "token_maker"
    assert normalize_function("token-producer") == "token_maker"
    assert normalize_function("card advantage") == "draw"
    assert normalize_function("counter") == "counterspell"


def test_normalize_functions_dedupes_after_aliasing():
    assert normalize_functions(["tokens", "token_maker", "card_advantage", "draw"]) == [
        "token_maker",
        "draw",
    ]


def test_classification_accepts_known_function_aliases():
    classification = CardClassification.model_validate(
        {
            **VALID_CLASSIFICATION,
            "functions": ["tokens", "sac_outlet", "card_advantage"],
        }
    )

    assert classification.functions == ["token_maker", "sacrifice_outlet", "draw"]


def test_role_groups_match_specialized_functions():
    assert role_bucket("creature_removal") == "removal"
    assert role_bucket("impulse_draw") == "draw"
    assert "creature_removal" in function_group("removal")
    assert has_function(["creature_removal"], "removal")
    assert has_function(["card_advantage"], "draw")


def test_package_health_counts_aliases():
    health = check_package_health(
        "aristocrats",
        ["sac_outlet", "free_sacrifice", "death_trigger", "tokens", "recursion"],
    )

    assert health is not None
    assert health.enabler_count == 2
    assert health.payoff_count == 1
    assert health.support_count == 2
