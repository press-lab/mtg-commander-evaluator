import pytest

from tests.conftest import SAMPLE_CARDS
from mtg_evaluator.normalization.normalizer import _build_card
from mtg_evaluator.ingestion.scryfall import compute_oracle_text_checksum
from datetime import datetime


NOW = datetime(2024, 6, 1, 12, 0, 0)


def build(card_json):
    oracle_id = card_json["oracle_id"]
    checksum = compute_oracle_text_checksum(card_json)
    return _build_card(card_json, oracle_id, checksum, card_json["type_line"], NOW, None)


class TestTypeParsing:
    def test_instant_flagged(self):
        card = next(c for c in SAMPLE_CARDS if c["name"] == "Swords to Plowshares")
        result = build(card)
        assert result["is_instant"] is True
        assert result["is_sorcery"] is False
        assert result["is_creature"] is False
        assert result["is_land"] is False

    def test_legendary_planeswalker(self):
        card = next(c for c in SAMPLE_CARDS if "Teferi" in c["name"])
        result = build(card)
        assert result["is_legendary"] is True
        assert result["is_planeswalker"] is True
        assert result["is_creature"] is False

    def test_sorcery_flagged(self):
        card = next(c for c in SAMPLE_CARDS if c["name"] == "Cultivate")
        result = build(card)
        assert result["is_sorcery"] is True
        assert result["is_instant"] is False

    def test_transform_creature(self):
        card = next(c for c in SAMPLE_CARDS if "Delver" in c["name"])
        result = build(card)
        assert result["is_creature"] is True

    def test_artifact_not_flagged_as_creature(self):
        card = next(c for c in SAMPLE_CARDS if c["name"] == "Sol Ring")
        result = build(card)
        assert result["is_creature"] is False
        assert result["is_land"] is False
        assert result["is_legendary"] is False


class TestColorIdentity:
    def test_colors_sorted(self):
        card = next(c for c in SAMPLE_CARDS if "Teferi" in c["name"])
        result = build(card)
        assert result["colors"] == sorted(result["colors"])
        assert result["color_identity"] == sorted(result["color_identity"])

    def test_colorless_card(self):
        card = next(c for c in SAMPLE_CARDS if c["name"] == "Sol Ring")
        result = build(card)
        assert result["colors"] == []
        assert result["color_identity"] == []


class TestMultiFaceCards:
    def test_image_uri_from_first_face(self):
        card = next(c for c in SAMPLE_CARDS if "Delver" in c["name"])
        result = build(card)
        assert result["image_uri"] is not None
        assert "front" in result["image_uri"]

    def test_layout_preserved(self):
        card = next(c for c in SAMPLE_CARDS if "Delver" in c["name"])
        result = build(card)
        assert result["layout"] == "transform"


class TestChecksumField:
    def test_checksum_stored_in_card(self):
        card = SAMPLE_CARDS[0]
        result = build(card)
        expected = compute_oracle_text_checksum(card)
        assert result["oracle_text_checksum"] == expected

    def test_first_seen_at_set_for_new_card(self):
        card = SAMPLE_CARDS[0]
        result = build(card)
        assert result["first_seen_at"] == NOW

    def test_first_seen_at_preserved_for_existing(self):
        card = SAMPLE_CARDS[0]
        oracle_id = card["oracle_id"]
        checksum = compute_oracle_text_checksum(card)
        first_seen = datetime(2023, 1, 1)
        result = _build_card(card, oracle_id, checksum, card["type_line"], NOW, first_seen)
        assert result["first_seen_at"] == datetime(2023, 1, 1)
