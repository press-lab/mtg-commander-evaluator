"""Unit tests for the deterministic skeleton solver and EDHREC comparison."""

from mtg_evaluator.deckbuilding.edhrec_compare import compare_to_edhrec
from mtg_evaluator.deckbuilding.pool import CardPool, PoolCard
from mtg_evaluator.deckbuilding.request import DeckRequest
from mtg_evaluator.deckbuilding.role_targets import RoleTargets
from mtg_evaluator.deckbuilding.skeleton import (
    BASIC_LAND_NAMES,
    _allocate_basics,
    build_skeleton,
)


def _card(
    name: str,
    type_line: str = "Creature",
    functions: list[str] | None = None,
    score: float = 50.0,
    arch: float | None = 3.0,
) -> PoolCard:
    return PoolCard(
        name=name,
        oracle_id=f"{name}-oid",
        tier="SUPPORT",
        score=score,
        archetype_score=arch,
        bracket_score=3.0,
        role_quality=3.0,
        functions=functions or [],
        is_game_changer=False,
        combo_ids=[],
        edhrec_decks=1000,
        type_line=type_line,
        cmc=2.0,
    )


def _pool(cards: list[PoolCard], *, colors=None, targets=None) -> CardPool:
    pool = CardPool(
        request=DeckRequest(commander_name="Test Commander"),
        commander_name="Test Commander",
        color_identity=colors if colors is not None else ["B", "G"],
        archetype="midrange",
        commander_mana_cost="{1}{B}{G}",
        commander_cmc=3.0,
    )
    pool.support = cards
    pool.role_targets = targets or RoleTargets(
        lands=36, ramp=10, draw=8, removal=6,
        board_wipe=2, protection=2, finisher=3,
    )
    return pool


class TestSkeleton:
    def _rich_pool(self) -> CardPool:
        cards = []
        for i in range(20):
            cards.append(_card(f"Land {i}", "Land", score=90 - i))
        for i in range(15):
            cards.append(_card(f"Ramp {i}", "Artifact", ["ramp"], score=80 - i))
        for i in range(12):
            cards.append(_card(f"Draw {i}", "Sorcery", ["draw"], score=75 - i))
        for i in range(10):
            cards.append(_card(f"Removal {i}", "Instant", ["removal"], score=70 - i))
        for i in range(4):
            cards.append(_card(f"Wipe {i}", "Sorcery", ["board_wipe"], score=65 - i))
        for i in range(4):
            cards.append(_card(f"Protect {i}", "Instant", ["protection"], score=60 - i))
        for i in range(5):
            cards.append(_card(f"Finisher {i}", "Creature", ["finisher"], score=55 - i))
        for i in range(40):
            cards.append(_card(f"Synergy {i}", "Creature", [], score=50 - i))
        return _pool(cards)

    def test_exact_deck_size(self):
        deck = build_skeleton(self._rich_pool())
        assert deck.total == 99

    def test_partner_deck_is_98(self):
        pool = self._rich_pool()
        pool.commander_name = "Tymna + Thrasios"
        deck = build_skeleton(pool)
        assert deck.total == 98

    def test_role_targets_met(self):
        deck = build_skeleton(self._rich_pool())
        assert deck.role_fill["lands"] == 36
        assert deck.role_fill["ramp"] >= 10
        assert deck.role_fill["draw"] >= 8
        assert deck.role_fill["removal"] >= 6

    def test_basics_fill_land_shortfall(self):
        # Only 5 nonbasic lands in the pool — basics must cover the rest
        cards = [_card(f"Land {i}", "Land", score=90) for i in range(5)]
        cards += [_card(f"Filler {i}", "Creature", score=40) for i in range(80)]
        deck = build_skeleton(_pool(cards))
        assert len(deck.lands) == 36
        basics = [n for n in deck.lands if n in BASIC_LAND_NAMES]
        assert len(basics) >= 31
        # Only identity-color basics
        assert set(basics) <= {"Swamp", "Forest"}

    def test_double_duty_credits_both_roles(self):
        # One card that is both ramp and draw should reduce both needs
        cards = [_card("Double Duty", "Creature", ["ramp", "draw"], score=99)]
        cards += [_card(f"Ramp {i}", "Artifact", ["ramp"], score=70) for i in range(12)]
        cards += [_card(f"Draw {i}", "Sorcery", ["draw"], score=70) for i in range(10)]
        cards += [_card(f"Filler {i}", "Creature", score=40) for i in range(80)]
        deck = build_skeleton(_pool(cards))
        assert "Double Duty" in deck.all_cards
        # Credited to both roles
        assert deck.role_fill["ramp"] >= 10
        assert deck.role_fill["draw"] >= 8

    def test_best_scores_picked_first(self):
        deck = build_skeleton(self._rich_pool())
        # Highest-scoring ramp must be in the deck before lower ones
        assert "Ramp 0" in deck.all_cards
        assert "Draw 0" in deck.all_cards
        assert "Removal 0" in deck.all_cards

    def test_rationale_recorded(self):
        deck = build_skeleton(self._rich_pool())
        assert deck.rationale.get("Ramp 0", "").startswith("ramp")


class TestAllocateBasics:
    def test_colorless_commander_gets_wastes(self):
        pool = _pool([], colors=[])
        assert _allocate_basics([], 5, pool) == ["Wastes"] * 5

    def test_two_color_split(self):
        pool = _pool([], colors=["B", "G"])
        basics = _allocate_basics([], 10, pool)
        assert len(basics) == 10
        assert set(basics) <= {"Swamp", "Forest"}
        # Both colors represented
        assert "Swamp" in basics and "Forest" in basics


class TestEdhrecCompare:
    def test_no_stats_returns_none(self):
        pool = _pool([])
        assert compare_to_edhrec(["Sol Ring"], pool, {}) is None

    def test_overlap_and_miss_reasons(self):
        in_pool = _card("Pool Card", "Creature", score=44)
        pool = _pool([in_pool])
        cmd_stats = {
            "Matched Card": {"inclusion_rate": 0.8, "synergy": 0.5},
            "Pool Card": {"inclusion_rate": 0.7, "synergy": 0.3},
            "Absent Card": {"inclusion_rate": 0.5, "synergy": 0.2},
            "Fringe Card": {"inclusion_rate": 0.05, "synergy": 0.0},
        }
        cmp = compare_to_edhrec(
            ["Matched Card", "Other Pick"],
            pool,
            cmd_stats,
            classified_names={"Pool Card"},
        )
        assert cmp is not None
        assert cmp.consensus_size == 3  # >= 40% inclusion
        assert cmp.matched == ["Matched Card"]
        reasons = {m.name: m.reason for m in cmp.missed}
        assert reasons["Pool Card"] == "outscored"  # in pool, not picked
        assert reasons["Absent Card"] == "not_classified"
        assert round(cmp.overlap_pct, 2) == 0.33
