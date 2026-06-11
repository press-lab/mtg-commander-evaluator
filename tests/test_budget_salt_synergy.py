"""Unit tests for budget filtering, salt scores, and per-commander EDHREC data."""

from datetime import datetime

from mtg_evaluator.deckbuilding.request import DeckRequest, SALT_THRESHOLDS
from mtg_evaluator.ingestion.edhrec import _extract_salt
from mtg_evaluator.ingestion.edhrec_commander import (
    CommanderCardStat,
    _parse_commander_page,
    commander_slug,
)
from mtg_evaluator.normalization.normalizer import _build_card


class TestCommanderSlug:
    def test_simple_name(self):
        assert commander_slug("Krenko, Mob Boss") == "krenko-mob-boss"

    def test_quotes_and_punctuation(self):
        assert commander_slug('Henzie "Toolbox" Torre') == "henzie-toolbox-torre"

    def test_apostrophe(self):
        assert commander_slug("Atraxa, Praetors' Voice") == "atraxa-praetors-voice"

    def test_dfc_uses_front_face(self):
        assert (
            commander_slug("Tergrid, God of Fright // Tergrid's Lantern")
            == "tergrid-god-of-fright"
        )

    def test_partner_pair(self):
        assert (
            commander_slug("Akiri, Line-Slinger", "Silas Renn, Seeker Adept")
            == "akiri-line-slinger-silas-renn-seeker-adept"
        )

    def test_accented_characters(self):
        assert commander_slug("Ghired, Conclave Exile") == "ghired-conclave-exile"


class TestSaltExtraction:
    def test_salt_field(self):
        assert _extract_salt({"salt": 3.14}) == 3.14

    def test_salt_label(self):
        assert _extract_salt({"label": "Salt Score: 2.75"}) == 2.75

    def test_no_salt(self):
        assert _extract_salt({"name": "Sol Ring"}) is None

    def test_thresholds_ordered(self):
        assert SALT_THRESHOLDS["low"] < SALT_THRESHOLDS["medium"] < SALT_THRESHOLDS["high"]


class TestDeckRequestDefaults:
    def test_no_budget_by_default(self):
        req = DeckRequest(commander_name="Test")
        assert req.max_card_price is None
        assert req.salt_tolerance == "any"

    def test_budget_and_salt_set(self):
        req = DeckRequest(
            commander_name="Test", max_card_price=5.0, salt_tolerance="low"
        )
        assert req.max_card_price == 5.0
        assert req.salt_tolerance == "low"


class TestCommanderPageParsing:
    def test_parse_standard_page(self):
        data = {
            "container": {
                "json_dict": {
                    "cardlists": [
                        {
                            "tag": "highsynergycards",
                            "cardviews": [
                                {
                                    "name": "Solemn Simulacrum",
                                    "num_decks": 770,
                                    "potential_decks": 1000,
                                    "synergy": 0.64,
                                }
                            ],
                        },
                        {
                            "tag": "topcards",
                            "cardviews": [
                                # Duplicate name across lists is deduped
                                {
                                    "name": "Solemn Simulacrum",
                                    "num_decks": 770,
                                    "potential_decks": 1000,
                                    "synergy": 0.64,
                                },
                                {
                                    "name": "Arcane Signet",
                                    "num_decks": 600,
                                    "potential_decks": 1000,
                                    "synergy": -0.1,
                                },
                            ],
                        },
                    ]
                }
            }
        }
        stats = _parse_commander_page(data)
        assert len(stats) == 2
        solemn = next(s for s in stats if s.card_name == "Solemn Simulacrum")
        assert solemn.inclusion_rate == 0.77
        assert solemn.synergy == 0.64
        assert solemn.category == "highsynergycards"
        signet = next(s for s in stats if s.card_name == "Arcane Signet")
        assert signet.synergy == -0.1

    def test_parse_malformed_page(self):
        assert _parse_commander_page({}) == []
        assert _parse_commander_page({"container": {}}) == []

    def test_zero_potential_decks(self):
        data = {
            "container": {
                "json_dict": {
                    "cardlists": [
                        {
                            "tag": "newcards",
                            "cardviews": [
                                {"name": "X", "num_decks": 5, "potential_decks": 0}
                            ],
                        }
                    ]
                }
            }
        }
        stats = _parse_commander_page(data)
        assert stats[0].inclusion_rate == 0.0


class TestPriceNormalization:
    def _base_json(self, prices: dict | None) -> dict:
        return {
            "id": "9b584d52-2c4f-4f93-9d6c-85a0a14e9f29",
            "name": "Test Card",
            "cmc": 2.0,
            "scryfall_uri": "https://scryfall.com/x",
            "prices": prices,
        }

    def _build(self, prices: dict | None) -> dict:
        return _build_card(
            self._base_json(prices),
            oracle_id="a2daf943-dc88-4c8b-ac97-4476ea6abb9c",
            checksum="x" * 64,
            type_line="Instant",
            now=datetime(2026, 1, 1),
            first_seen=None,
        )

    def test_usd_price(self):
        assert self._build({"usd": "3.50"})["price_usd"] == 3.50

    def test_foil_fallback(self):
        assert self._build({"usd": None, "usd_foil": "12.99"})["price_usd"] == 12.99

    def test_no_prices(self):
        assert self._build(None)["price_usd"] is None
        assert self._build({"usd": None})["price_usd"] is None

    def test_garbage_price(self):
        assert self._build({"usd": "not-a-number"})["price_usd"] is None


class TestCommanderCardStatDataclass:
    def test_fields(self):
        s = CommanderCardStat(
            card_name="X",
            num_decks=10,
            potential_decks=100,
            inclusion_rate=0.1,
            synergy=0.05,
            category="topcards",
        )
        assert s.inclusion_rate == 0.1
