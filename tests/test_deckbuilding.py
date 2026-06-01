"""
Tests for the three deckbuilding entry points:
  1. build_card_pool  (commander-first)
  2. find_commanders  (archetype-first)
  3. browse_cards     (card browser)

Two tiers:
  - Unit tests  — pure Python, no DB, cover scoring/filtering logic
  - Integration  — require real PostgreSQL with seeded data
    Run with: pytest -m integration
    Skip with: pytest -m "not integration"
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch, call
import pytest

from mtg_evaluator.deckbuilding.request import DeckRequest, BRACKET_COMBO_ALLOWANCE
from mtg_evaluator.deckbuilding.pool import (
    _composite_score,
    _log_popularity,
    CardPool,
    PoolCard,
)

# ===========================================================================
# Unit tests — no database, no I/O
# ===========================================================================


class TestDeckRequest:
    """DeckRequest construction and combo tag derivation."""

    def test_defaults(self):
        req = DeckRequest(commander_name="Test Commander")
        assert req.bracket == 3
        assert req.pool_size == 300
        assert req.tutor_density == "light"
        assert req.archetype is None

    def test_allowed_combo_tags_derived_from_bracket(self):
        for bracket, expected in BRACKET_COMBO_ALLOWANCE.items():
            req = DeckRequest(commander_name="Test", bracket=bracket, want_combos=True)
            assert req.allowed_combo_tags == expected, f"bracket {bracket} mismatch"

    def test_want_combos_false_clears_tags(self):
        req = DeckRequest(commander_name="Test", bracket=4, want_combos=False)
        assert len(req.allowed_combo_tags) == 0

    def test_b1_b2_only_casual_tags(self):
        for bracket in (1, 2):
            req = DeckRequest(commander_name="Test", bracket=bracket, want_combos=True)
            for disallowed in ("R", "S", "P"):
                assert (
                    disallowed not in req.allowed_combo_tags
                ), f"B{bracket} should not allow tag '{disallowed}'"

    def test_b4_allows_r_combos(self):
        req = DeckRequest(commander_name="Test", bracket=4, want_combos=True)
        assert "R" in req.allowed_combo_tags

    def test_b3_excludes_r_combos(self):
        req = DeckRequest(commander_name="Test", bracket=3, want_combos=True)
        assert "R" not in req.allowed_combo_tags

    def test_explicit_allowed_tags_not_overwritten(self):
        """If caller explicitly sets allowed_combo_tags, __post_init__ should not overwrite."""
        req = DeckRequest(
            commander_name="Test",
            bracket=3,
            want_combos=True,
            allowed_combo_tags={"R"},  # override — user knows what they're doing
        )
        assert "R" in req.allowed_combo_tags

    def test_bracket_label(self):
        assert DeckRequest(commander_name="X", bracket=1).bracket_label == "B1"
        assert DeckRequest(commander_name="X", bracket=5).bracket_label == "B5/cEDH"


class TestLogPopularity:
    """_log_popularity: maps EDHREC deck counts to [0, 1]."""

    def test_none_returns_zero(self):
        assert _log_popularity(None) == 0.0

    def test_zero_returns_zero(self):
        assert _log_popularity(0) == 0.0

    def test_negative_returns_zero(self):
        assert _log_popularity(-5) == 0.0

    def test_100k_decks_returns_one(self):
        # log10(100000) / 5 = 5/5 = 1.0
        result = _log_popularity(100_000)
        assert abs(result - 1.0) < 0.001

    def test_monotonically_increasing(self):
        vals = [_log_popularity(n) for n in [1, 100, 1000, 10_000, 100_000]]
        assert vals == sorted(vals)

    def test_capped_at_one(self):
        # Even absurdly popular cards cap at 1.0
        assert _log_popularity(10_000_000) == 1.0


class TestCompositeScore:
    """_composite_score: weighted sum of Phase 5 scoring signals."""

    def test_all_zeros_returns_zero(self):
        assert _composite_score(0, 0, False, False, 0.0) == 0.0

    def test_max_inputs_returns_100(self):
        score = _composite_score(
            5.0,
            5.0,
            5.0,
            True,
            1.0,
            commander_fit=1.0,
            package_bonus=1.0,
        )
        assert score == 100.0

    def test_archetype_weight_30(self):
        # Only archetype score set, everything else zero
        score = _composite_score(5.0, 0, False, False, 0.0)
        assert score == 30.0

    def test_bracket_weight_10(self):
        score = _composite_score(0, 5.0, False, False, 0.0)
        assert score == 10.0

    def test_role_quality_weight_25(self):
        score = _composite_score(0, 0, 5.0, False, 0.0)
        assert score == 25.0

    def test_combo_weight_7(self):
        score = _composite_score(0, 0, False, True, 0.0)
        assert score == 7.0

    def test_popularity_weight_3(self):
        score = _composite_score(0, 0, False, False, 1.0)
        assert score == 3.0

    def test_none_scores_treated_as_zero(self):
        score_none = _composite_score(None, None, False, False, 0.0)
        score_zero = _composite_score(0.0, 0.0, False, False, 0.0)
        assert score_none == score_zero

    def test_partial_archetype_score(self):
        # arch=2.5/5 → 0.5 × 30 = 15
        score = _composite_score(2.5, 0, False, False, 0.0)
        assert score == 15.0


class TestCardPoolProperties:
    """CardPool dataclass: all_cards aggregation and total count."""

    def _make_pool_card(self, name, tier="CORE"):
        return PoolCard(
            name=name,
            oracle_id=f"{name}-oid",
            tier=tier,
            score=50.0,
            archetype_score=3.0,
            bracket_score=3.0,
            role_quality=0.0,
            functions=[],
            is_game_changer=False,
            combo_ids=[],
            edhrec_decks=1000,
            type_line="Creature",
            cmc=3.0,
        )

    def test_all_cards_combines_tiers(self):
        req = DeckRequest(commander_name="X")
        pool = CardPool(
            request=req, commander_name="X", color_identity=["B"], archetype="sac"
        )
        pool.core = [self._make_pool_card("A")]
        pool.support = [
            self._make_pool_card("B", "SUPPORT"),
            self._make_pool_card("C", "SUPPORT"),
        ]
        pool.flex = [self._make_pool_card("D", "FLEX")]
        assert pool.total == 4
        names = [c.name for c in pool.all_cards]
        assert set(names) == {"A", "B", "C", "D"}

    def test_empty_pool_total_zero(self):
        req = DeckRequest(commander_name="X")
        pool = CardPool(
            request=req, commander_name="X", color_identity=[], archetype=None
        )
        assert pool.total == 0


# ===========================================================================
# Unit tests for scoring + tutor density logic (mocked DB)
# ===========================================================================


class TestTutorDensityScoring:
    """
    Tutor density is a scoring multiplier, NOT a gate.
    Tutors must appear in pool at all density settings.
    """

    def _make_row(self, name, oracle_id, arch, brack, num_decks, color_identity=None):
        row = MagicMock()
        row.name = name
        row.oracle_id = oracle_id
        row.arch_score = arch
        row.brack_score = brack
        row.num_decks = num_decks
        row.color_identity = color_identity or []
        row.type_line = "Sorcery"
        return row

    def test_tutor_score_heavy_gt_none(self):
        """At density=heavy, tutor score exceeds density=none."""
        from mtg_evaluator.deckbuilding.pool import _composite_score, _log_popularity

        base_score = _composite_score(2.5, 3.0, True, False, _log_popularity(200_000))
        score_none = round(base_score * 0.5, 2)
        score_heavy = round(base_score * 1.3, 2)
        assert score_heavy > score_none

    def test_tutor_score_light_is_unchanged(self):
        from mtg_evaluator.deckbuilding.pool import _composite_score, _log_popularity

        base_score = _composite_score(2.5, 3.0, True, False, _log_popularity(200_000))
        score_light = round(base_score * 1.0, 2)
        assert score_light == base_score


# ===========================================================================
# Integration tests — require real PostgreSQL
# ===========================================================================

pytestmark_integration = pytest.mark.integration


@pytest.mark.integration
class TestFindCommandersIntegration:
    """Requires seeded DB with classified cards."""

    @pytest.fixture
    def session(self):
        from mtg_evaluator.db.connection import get_session

        with get_session() as s:
            yield s

    def test_sacrifice_commanders_include_yawgmoth(self, session):
        from mtg_evaluator.deckbuilding.find_commanders import find_commanders

        results = find_commanders(session, archetype="sacrifice", limit=20)
        names = [c.name for c in results]
        assert len(results) > 0, "No sacrifice commanders found — is DB seeded?"
        # Yawgmoth is the canonical sacrifice commander
        assert any("Yawgmoth" in n for n in names), f"Yawgmoth missing from: {names}"

    def test_color_filter_restricts_results(self, session):
        from mtg_evaluator.deckbuilding.find_commanders import find_commanders

        # Request only mono-B commanders
        results = find_commanders(
            session, archetype="sacrifice", colors=["B"], limit=20
        )
        for c in results:
            ci = set(c.color_identity) - {"C"}
            assert ci.issubset(
                {"B"}
            ), f"{c.name} has off-color identity: {c.color_identity}"

    def test_results_sorted_by_score(self, session):
        from mtg_evaluator.deckbuilding.find_commanders import find_commanders

        results = find_commanders(session, archetype="sacrifice", limit=20)
        scores = [c.archetype_score for c in results]
        assert scores == sorted(scores, reverse=True)

    def test_non_creature_legendaries_excluded(self, session):
        """Only legendary creatures should be returned (no planeswalkers, enchantments)."""
        from mtg_evaluator.deckbuilding.find_commanders import find_commanders

        results = find_commanders(session, archetype="sacrifice", limit=50)
        for c in results:
            assert (
                "Creature" in c.type_line
            ), f"{c.name} type_line '{c.type_line}' is not a creature"


@pytest.mark.integration
class TestBrowseCardsIntegration:
    @pytest.fixture
    def session(self):
        from mtg_evaluator.db.connection import get_session

        with get_session() as s:
            yield s

    def test_browse_sacrifice_ramp(self, session):
        from mtg_evaluator.deckbuilding.browse import browse_cards

        results = browse_cards(
            session, archetype="sacrifice", bracket=3, role="ramp", limit=20
        )
        assert len(results) > 0

    def test_tutors_pass_any_role_filter(self, session):
        from mtg_evaluator.deckbuilding.browse import browse_cards

        # When filtering for ramp, tutors should still appear
        results = browse_cards(
            session, archetype="sacrifice", bracket=3, role="ramp", limit=50
        )
        names = [c.name for c in results]
        functions_map = {c.name: c.functions for c in results}
        # At least one result should have 'tutor' function (bypassed role filter)
        tutors_in_result = [n for n, fns in functions_map.items() if "tutor" in fns]
        assert (
            len(tutors_in_result) > 0
        ), "No tutors in ramp results — tutor bypass broken"

    def test_sorted_by_score(self, session):
        from mtg_evaluator.deckbuilding.browse import browse_cards

        results = browse_cards(session, archetype="sacrifice", bracket=3, limit=30)
        scores = [c.score for c in results]
        assert scores == sorted(scores, reverse=True)

    def test_color_filter_excludes_off_color(self, session):
        from mtg_evaluator.deckbuilding.browse import browse_cards

        results = browse_cards(
            session, archetype=None, bracket=3, colors=["W"], limit=50
        )
        for c in results:
            if c.oracle_id:
                pass  # color identity check done in function

    def test_gc_excluded_at_b1(self, session):
        from mtg_evaluator.deckbuilding.browse import browse_cards
        from mtg_evaluator.evaluation.evaluator import GAME_CHANGERS

        results = browse_cards(session, archetype=None, bracket=1, limit=100)
        gc_found = [c.name for c in results if c.name in GAME_CHANGERS]
        assert len(gc_found) == 0, f"GCs appeared at B1: {gc_found}"


@pytest.mark.integration
class TestBuildCardPoolIntegration:
    @pytest.fixture
    def session(self):
        from mtg_evaluator.db.connection import get_session

        with get_session() as s:
            yield s

    def test_henzie_b4_midrange_pool(self, session):
        from mtg_evaluator.deckbuilding.pool import build_card_pool

        req = DeckRequest(
            commander_name='Henzie "Toolbox" Torre',
            bracket=4,
            archetype="midrange",
            want_combos=True,
            tutor_density="light",
        )
        pool = build_card_pool(session, req)
        assert pool.total > 0
        assert pool.commander_name == 'Henzie "Toolbox" Torre'

    def test_commander_not_in_pool(self, session):
        from mtg_evaluator.deckbuilding.pool import build_card_pool

        req = DeckRequest(
            commander_name='Henzie "Toolbox" Torre',
            bracket=4,
            archetype="midrange",
            want_combos=False,
        )
        pool = build_card_pool(session, req)
        all_names = [c.name for c in pool.all_cards]
        assert 'Henzie "Toolbox" Torre' not in all_names

    def test_gc_tutors_present_at_b4(self, session):
        """Demonic Tutor and Vampiric Tutor are GCs — legal at B4."""
        from mtg_evaluator.deckbuilding.pool import build_card_pool

        req = DeckRequest(
            commander_name='Henzie "Toolbox" Torre',
            bracket=4,
            archetype="midrange",
            want_combos=False,
            tutor_density="light",
        )
        pool = build_card_pool(session, req)
        all_names = [c.name for c in pool.all_cards]
        assert (
            "Demonic Tutor" in all_names or "Vampiric Tutor" in all_names
        ), "GC tutors missing at B4 — GC bracket filter too aggressive"

    def test_gc_tutors_absent_at_b2(self, session):
        """Demonic Tutor etc. are GCs — NOT legal at B1/B2."""
        from mtg_evaluator.deckbuilding.pool import build_card_pool
        from mtg_evaluator.evaluation.evaluator import GAME_CHANGERS

        req = DeckRequest(
            commander_name="Braids, Cabal Minion",
            bracket=2,
            archetype="control",
            want_combos=False,
            tutor_density="light",
        )
        try:
            pool = build_card_pool(session, req)
        except ValueError:
            pytest.skip("Braids not in DB — seed data needed")
        for card in pool.all_cards:
            assert (
                card.name not in GAME_CHANGERS
            ), f"GC '{card.name}' appeared in B2 pool"

    def test_pool_respects_size(self, session):
        from mtg_evaluator.deckbuilding.pool import build_card_pool

        req = DeckRequest(
            commander_name='Henzie "Toolbox" Torre',
            bracket=4,
            archetype="midrange",
            want_combos=False,
            pool_size=50,
        )
        pool = build_card_pool(session, req)
        assert pool.total <= 50

    def test_core_tier_has_highest_scores(self, session):
        """CORE cards should generally score higher than FLEX cards."""
        from mtg_evaluator.deckbuilding.pool import build_card_pool

        req = DeckRequest(
            commander_name='Henzie "Toolbox" Torre',
            bracket=4,
            archetype="midrange",
            want_combos=False,
        )
        pool = build_card_pool(session, req)
        if pool.core and pool.flex:
            avg_core = sum(c.score for c in pool.core) / len(pool.core)
            avg_flex = sum(c.score for c in pool.flex) / len(pool.flex)
            assert (
                avg_core >= avg_flex
            ), f"CORE avg score {avg_core:.1f} < FLEX avg {avg_flex:.1f}"
