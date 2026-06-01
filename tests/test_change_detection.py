from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from mtg_evaluator.change_detection.detector import detect_changes, _create_job
from mtg_evaluator.db.models import JobStatus


NOW = datetime(2024, 6, 1, 12, 0, 0)


def _make_raw(oracle_id: str, checksum: str):
    raw = MagicMock()
    raw.oracle_id = oracle_id
    raw.oracle_text_checksum = checksum
    return raw


def _make_card(oracle_id: str, checksum: str):
    card = MagicMock()
    card.oracle_id = oracle_id
    card.oracle_text_checksum = checksum
    return card


def _make_run(run_id="run-1"):
    run = MagicMock()
    run.id = run_id
    return run


class TestDetectChanges:
    def _session_with(self, run, raw_cards, existing_cards: dict, existing_jobs=None):
        session = MagicMock()
        session.get = MagicMock(side_effect=lambda model, pk: existing_cards.get(pk))
        session.scalars = MagicMock()

        run_result = MagicMock()
        run_result.first.return_value = run
        raw_result = MagicMock()
        raw_result.__iter__ = MagicMock(return_value=iter(raw_cards))

        def scalars_side_effect(stmt):
            result = MagicMock()
            result.all.return_value = raw_cards
            result.first.return_value = None
            return result

        session.scalars.side_effect = scalars_side_effect
        return session

    def test_new_card_creates_job(self):
        oracle_id = "aaaa-1111"
        raw_cards = [_make_raw(oracle_id, "abc123")]
        run = _make_run()

        jobs_added = []
        session = MagicMock()
        session.get.side_effect = lambda model, pk: None

        run_scalars = MagicMock()
        run_scalars.first.return_value = run
        raw_scalars = MagicMock()
        raw_scalars.all.return_value = raw_cards

        call_count = [0]
        def scalars_side_effect(stmt):
            call_count[0] += 1
            if call_count[0] == 1:
                return run_scalars
            return raw_scalars

        session.scalars.side_effect = scalars_side_effect
        session.add.side_effect = jobs_added.append
        session.flush = MagicMock()

        result = detect_changes(session)

        assert result.new_cards == 1
        assert result.changed_cards == 0
        assert result.jobs_created == 1
        assert len(jobs_added) == 1

    def test_unchanged_card_does_not_create_job(self):
        oracle_id = "bbbb-2222"
        checksum = "same_checksum"
        raw_cards = [_make_raw(oracle_id, checksum)]
        existing_card = _make_card(oracle_id, checksum)
        run = _make_run()

        jobs_added = []
        session = MagicMock()
        session.get.side_effect = lambda model, pk: existing_card if pk == oracle_id else None

        run_scalars = MagicMock()
        run_scalars.first.return_value = run
        raw_scalars = MagicMock()
        raw_scalars.all.return_value = raw_cards

        call_count = [0]
        def scalars_side_effect(stmt):
            call_count[0] += 1
            if call_count[0] == 1:
                return run_scalars
            return raw_scalars

        # oracle_ids_with_jobs: card already has a job
        execute_result = MagicMock()
        execute_result.all.return_value = [(oracle_id,)]
        session.execute.return_value = execute_result

        session.scalars.side_effect = scalars_side_effect
        session.add.side_effect = jobs_added.append
        session.flush = MagicMock()

        result = detect_changes(session)

        assert result.unchanged_cards == 1
        assert result.new_cards == 0
        assert result.jobs_created == 0
        assert len(jobs_added) == 0

    def test_changed_card_creates_job(self):
        oracle_id = "cccc-3333"
        raw_cards = [_make_raw(oracle_id, "new_checksum")]
        existing_card = _make_card(oracle_id, "old_checksum")
        run = _make_run()

        jobs_added = []
        session = MagicMock()
        session.get.side_effect = lambda model, pk: existing_card if pk == oracle_id else None

        run_scalars = MagicMock()
        run_scalars.first.return_value = run
        raw_scalars = MagicMock()
        raw_scalars.all.return_value = raw_cards

        no_pending_job = MagicMock()
        no_pending_job.first.return_value = None

        call_count = [0]
        def scalars_side_effect(stmt):
            call_count[0] += 1
            if call_count[0] == 1:
                return run_scalars
            if call_count[0] == 2:
                return raw_scalars
            return no_pending_job

        session.scalars.side_effect = scalars_side_effect
        session.add.side_effect = jobs_added.append
        session.flush = MagicMock()

        result = detect_changes(session)

        assert result.changed_cards == 1
        assert result.new_cards == 0
        assert result.jobs_created == 1


class TestCreateJob:
    def test_job_has_pending_status(self):
        jobs = []
        session = MagicMock()
        session.add.side_effect = jobs.append

        _create_job(session, "oracle-id-1", "checksum-abc", NOW)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.status == JobStatus.pending
        assert job.oracle_id == "oracle-id-1"
        assert job.oracle_text_checksum == "checksum-abc"
        assert job.attempt_count == 0
        assert job.created_at == NOW
