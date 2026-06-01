import json
from unittest.mock import MagicMock, patch

import pytest

from mtg_evaluator.ingestion.scryfall import compute_oracle_text_checksum


class TestOracleTextChecksum:
    def test_single_face_card(self):
        card = {"oracle_text": "Exile target creature."}
        checksum = compute_oracle_text_checksum(card)
        assert len(checksum) == 64
        assert checksum == compute_oracle_text_checksum(card)

    def test_different_oracle_text_produces_different_checksum(self):
        card_a = {"oracle_text": "Exile target creature."}
        card_b = {"oracle_text": "Destroy target creature."}
        assert compute_oracle_text_checksum(card_a) != compute_oracle_text_checksum(card_b)

    def test_same_oracle_text_produces_same_checksum(self):
        card_a = {"oracle_text": "Draw a card."}
        card_b = {"oracle_text": "Draw a card."}
        assert compute_oracle_text_checksum(card_a) == compute_oracle_text_checksum(card_b)

    def test_missing_oracle_text_does_not_crash(self):
        card = {}
        checksum = compute_oracle_text_checksum(card)
        assert len(checksum) == 64

    def test_multi_face_card_includes_all_faces(self):
        card_multi = {
            "oracle_text": "",
            "card_faces": [
                {"oracle_text": "Face A text."},
                {"oracle_text": "Face B text."},
            ],
        }
        card_single = {"oracle_text": "Face A text.Face B text."}
        assert compute_oracle_text_checksum(card_multi) == compute_oracle_text_checksum(card_single)

    def test_checksum_is_hex_sha256(self):
        card = {"oracle_text": "test"}
        checksum = compute_oracle_text_checksum(card)
        assert all(c in "0123456789abcdef" for c in checksum)


class TestFetchBulkMetadata:
    def test_parses_oracle_cards_entry(self):
        mock_response_data = {
            "data": [
                {"type": "all_cards", "download_uri": "https://example.com/all.json"},
                {"type": "oracle_cards", "download_uri": "https://example.com/oracle.json"},
            ]
        }

        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response_data
        mock_resp.raise_for_status = MagicMock()

        with patch("mtg_evaluator.ingestion.scryfall.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            with patch("mtg_evaluator.ingestion.scryfall._rate_limit"):
                from mtg_evaluator.ingestion.scryfall import fetch_bulk_metadata
                result = fetch_bulk_metadata()

        assert result["type"] == "oracle_cards"
        assert result["download_uri"] == "https://example.com/oracle.json"

    def test_raises_if_oracle_cards_not_found(self):
        mock_response_data = {"data": [{"type": "all_cards", "download_uri": "https://example.com/all.json"}]}

        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response_data
        mock_resp.raise_for_status = MagicMock()

        with patch("mtg_evaluator.ingestion.scryfall.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            with patch("mtg_evaluator.ingestion.scryfall._rate_limit"):
                from mtg_evaluator.ingestion.scryfall import fetch_bulk_metadata
                with pytest.raises(ValueError, match="oracle_cards"):
                    fetch_bulk_metadata()
