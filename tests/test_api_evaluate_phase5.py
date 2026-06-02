from types import SimpleNamespace

from mtg_evaluator.deckbuilding.consistency import ConsistencyReport
from mtg_evaluator.deckbuilding.packages import NonboWarning, PackageHealth
from mtg_evaluator.evaluation.deck_analysis import (
    RoleCoverage,
    phase5_score_fields,
    phase5_structure_score,
)


def _result(
    *,
    role_coverage=None,
    mana_reliability=0.95,
    consistency_score="good",
    package_healthy=True,
    nonbos=None,
    unresolved=0,
    unclassified=0,
):
    consistency_values = {
        "good": dict(
            lands_in_opener=0.9,
            ramp_by_turn3=0.85,
            commander_on_curve=0.8,
            interaction_by_turn5=0.85,
            enabler_payoff_by_turn6=0.75,
        ),
        "poor": dict(
            lands_in_opener=0.65,
            ramp_by_turn3=0.45,
            commander_on_curve=0.4,
            interaction_by_turn5=0.5,
            enabler_payoff_by_turn6=0.25,
        ),
    }[consistency_score]

    package_health = PackageHealth(
        archetype="sacrifice",
        description="Sacrifice shell",
        enabler_count=8 if package_healthy else 2,
        payoff_count=8 if package_healthy else 1,
        support_count=5,
        min_enablers=6,
        min_payoffs=5,
        enabler_deficit=0 if package_healthy else 4,
        payoff_deficit=0 if package_healthy else 4,
        ratio=1.0,
        ideal_ratio=1.0,
        is_healthy=package_healthy,
        warnings=[] if package_healthy else ["Package is thin"],
    )

    return SimpleNamespace(
        role_coverage=role_coverage
        or [
            RoleCoverage("lands", 36, 36, 3.5),
            RoleCoverage("ramp", 10, 10, 3.5),
            RoleCoverage("draw", 8, 8, 3.5),
            RoleCoverage("removal", 6, 6, 3.5),
        ],
        mana_analysis=SimpleNamespace(cast_reliability=mana_reliability),
        consistency=ConsistencyReport(
            **consistency_values,
            land_count=36,
            ramp_count=10,
            interaction_count=8,
            commander_cmc=3.0,
            deck_size=99,
        ),
        package_health=package_health,
        nonbo_warnings=nonbos or [],
        unresolved_cards=["Missing"] * unresolved,
        unclassified_cards=["Unknown"] * unclassified,
    )


def test_structure_score_drops_when_mana_reliability_worsens():
    good = phase5_structure_score(_result(mana_reliability=0.95), total_cards=99)
    poor = phase5_structure_score(_result(mana_reliability=0.35), total_cards=99)

    assert poor < good


def test_deck_score_matches_structure_score():
    fields = phase5_score_fields(_result(), total_cards=99)

    assert fields["deck_score"] == fields["structure_score"]


def test_structure_score_drops_when_role_gaps_worsen():
    good = phase5_structure_score(_result(), total_cards=99)
    poor = phase5_structure_score(
        _result(
            role_coverage=[
                RoleCoverage("lands", 30, 36, 3.5),
                RoleCoverage("ramp", 4, 10, 2.0),
                RoleCoverage("draw", 3, 8, 2.0),
                RoleCoverage("removal", 2, 6, 2.0),
            ]
        ),
        total_cards=99,
    )

    assert poor < good


def test_structure_score_drops_when_unresolved_or_unclassified_worsen():
    good = phase5_structure_score(_result(), total_cards=99)
    poor = phase5_structure_score(
        _result(unresolved=6, unclassified=6),
        total_cards=99,
    )

    assert poor < good


def test_structure_score_improves_or_maintains_with_healthy_package_balance():
    healthy = phase5_structure_score(_result(package_healthy=True), total_cards=99)
    unhealthy = phase5_structure_score(_result(package_healthy=False), total_cards=99)

    assert healthy >= unhealthy


def test_structure_score_drops_when_nonbos_worsen():
    good = phase5_structure_score(_result(), total_cards=99)
    poor = phase5_structure_score(
        _result(
            nonbos=[
                NonboWarning(
                    rule_id="test_nonbo",
                    severity="high",
                    confidence=0.9,
                    message="Cards fight each other.",
                )
            ]
        ),
        total_cards=99,
    )

    assert poor < good
