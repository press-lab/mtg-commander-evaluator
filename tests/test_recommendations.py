"""Unit tests for the evaluator recommendation engine."""

from mtg_evaluator.db.models import Card
from mtg_evaluator.evaluation.parser import ParsedCard
from mtg_evaluator.evaluation.recommendations import (
    _bracket_legal,
    _fits_colors,
    find_cut_candidates,
    find_missing_staples,
)

MLD = frozenset({"Armageddon", "Winter Orb"})


class TestBracketLegal:
    def test_game_changer_blocked_below_b3(self):
        assert not _bracket_legal("Demonic Tutor", 2, MLD)
        assert _bracket_legal("Demonic Tutor", 3, MLD)

    def test_mass_land_denial_blocked_below_b4(self):
        assert not _bracket_legal("Armageddon", 3, MLD)
        assert _bracket_legal("Armageddon", 4, MLD)

    def test_normal_card_always_legal(self):
        assert _bracket_legal("Llanowar Elves", 1, MLD)


class TestFitsColors:
    def test_subset_passes(self):
        assert _fits_colors(["B", "G"], {"B", "R", "G"})

    def test_off_color_fails(self):
        assert not _fits_colors(["W"], {"B", "R", "G"})

    def test_colorless_passes(self):
        assert _fits_colors([], {"B"})
        assert _fits_colors(None, {"B"})


def _card(oracle_id: str, name: str, *, is_land=False, colors=None) -> Card:
    card = Card()
    card.oracle_id = oracle_id
    card.name = name
    card.is_land = is_land
    card.color_identity = colors or []
    return card


def _parsed(oracle_id: str, name: str, *, is_commander=False) -> ParsedCard:
    return ParsedCard(
        raw_name=name,
        oracle_id=oracle_id,
        quantity=1,
        is_commander=is_commander,
    )


class TestCutCandidates:
    def test_two_strikes_required(self):
        cards = {
            "id1": _card("id1", "Bad Fit"),
            "id2": _card("id2", "One Strike Only"),
        }
        classifications = {
            # Two strikes: weak archetype fit AND no core role
            "id1": {"functions": ["anthem"], "archetype_scores": {"aristocrats": 1}},
            # One strike: weak fit, but fills ramp
            "id2": {"functions": ["ramp"], "archetype_scores": {"aristocrats": 1}},
        }
        parsed = [_parsed("id1", "Bad Fit"), _parsed("id2", "One Strike Only")]
        cuts = find_cut_candidates(
            parsed, classifications, cards, "aristocrats",
            cmd_stats={}, combo_oracle_ids=set(),
        )
        names = [c.name for c in cuts]
        assert "Bad Fit" in names
        assert "One Strike Only" not in names

    def test_commander_and_combo_pieces_never_cut(self):
        cards = {"id1": _card("id1", "My Commander"), "id2": _card("id2", "Combo Bit")}
        classifications = {
            "id1": {"functions": ["anthem"], "archetype_scores": {"tokens": 1}},
            "id2": {"functions": ["anthem"], "archetype_scores": {"tokens": 1}},
        }
        parsed = [
            _parsed("id1", "My Commander", is_commander=True),
            _parsed("id2", "Combo Bit"),
        ]
        cuts = find_cut_candidates(
            parsed, classifications, cards, "tokens",
            cmd_stats={}, combo_oracle_ids={"id2"},
        )
        assert cuts == []

    def test_unclassified_never_cut(self):
        cards = {"id1": _card("id1", "Mystery Card")}
        classifications = {"id1": {"functions": [], "archetype_scores": {}}}
        parsed = [_parsed("id1", "Mystery Card")]
        cuts = find_cut_candidates(
            parsed, classifications, cards, "tokens",
            cmd_stats={}, combo_oracle_ids=set(),
        )
        assert cuts == []

    def test_negative_synergy_counts_as_strike(self):
        cards = {"id1": _card("id1", "Off Plan")}
        classifications = {
            "id1": {"functions": ["anthem"], "archetype_scores": {"voltron": 3}},
        }
        parsed = [_parsed("id1", "Off Plan")]
        cuts = find_cut_candidates(
            parsed, classifications, cards, "voltron",
            cmd_stats={"Off Plan": {"inclusion_rate": 0.01, "synergy": -0.12}},
            combo_oracle_ids=set(),
        )
        # Strikes: no core role + negative synergy + rarely played = flagged
        assert len(cuts) == 1
        assert "negative synergy" in cuts[0].reason


class TestMissingStaples:
    def test_cards_in_deck_excluded(self, mocker):
        session = mocker.MagicMock()
        session.execute.return_value.all.return_value = []
        recs = find_missing_staples(
            session,
            cmd_stats={"Sol Ring": {"inclusion_rate": 0.9, "synergy": 0.1}},
            deck_card_names={"Sol Ring"},
            deck_colors={"R"},
            bracket_int=3,
            mass_land_denial=MLD,
        )
        assert recs == []

    def test_low_inclusion_low_synergy_excluded(self, mocker):
        session = mocker.MagicMock()
        session.execute.return_value.all.return_value = []
        recs = find_missing_staples(
            session,
            cmd_stats={"Fringe Card": {"inclusion_rate": 0.05, "synergy": 0.02}},
            deck_card_names=set(),
            deck_colors={"R"},
            bracket_int=3,
            mass_land_denial=MLD,
        )
        assert recs == []
        # No candidates → no DB query needed
        session.execute.assert_not_called()
