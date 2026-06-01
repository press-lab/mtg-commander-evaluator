from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from mtg_evaluator.db.models import (
    Card,
    RawScryfallCard,
    CardIngestionRun,
    CardClassificationJob,
    IngestionStatus,
    JobStatus,
)
from mtg_evaluator.ingestion.scryfall import compute_oracle_text_checksum


@dataclass
class ChangeDetectionResult:
    new_cards: int
    changed_cards: int
    unchanged_cards: int
    jobs_created: int


def detect_changes(
    session: Session, run_id: str | None = None
) -> ChangeDetectionResult:
    if run_id:
        run = session.get(CardIngestionRun, run_id)
    else:
        run = session.scalars(
            select(CardIngestionRun)
            .where(CardIngestionRun.status == IngestionStatus.completed)
            .order_by(CardIngestionRun.completed_at.desc())
        ).first()

    if not run:
        raise RuntimeError("No completed ingestion run found.")

    raw_cards = session.scalars(
        select(RawScryfallCard).where(RawScryfallCard.ingestion_run_id == run.id)
    ).all()

    new_count = 0
    changed_count = 0
    unchanged_count = 0
    jobs_created = 0
    now = datetime.utcnow()

    oracle_ids_with_jobs: set[str] = set(
        row[0]
        for row in session.execute(
            select(CardClassificationJob.oracle_id).distinct()
        ).all()
    )

    for raw in raw_cards:
        oracle_id = raw.oracle_id
        existing = session.get(Card, oracle_id)

        if not existing:
            new_count += 1
            _create_job(session, oracle_id, raw.oracle_text_checksum, now)
            jobs_created += 1
            continue

        if existing.oracle_text_checksum != raw.oracle_text_checksum:
            changed_count += 1
            if oracle_id not in oracle_ids_with_jobs:
                _create_job(session, oracle_id, raw.oracle_text_checksum, now)
                jobs_created += 1
        else:
            unchanged_count += 1
            if oracle_id not in oracle_ids_with_jobs:
                _create_job(session, oracle_id, raw.oracle_text_checksum, now)
                jobs_created += 1

    session.flush()

    print(
        f"Change detection: {new_count} new, {changed_count} changed, "
        f"{unchanged_count} unchanged. {jobs_created} jobs created."
    )
    return ChangeDetectionResult(
        new_cards=new_count,
        changed_cards=changed_count,
        unchanged_cards=unchanged_count,
        jobs_created=jobs_created,
    )


def _create_job(session: Session, oracle_id: str, checksum: str, now: datetime) -> None:
    job = CardClassificationJob(
        oracle_id=oracle_id,
        status=JobStatus.pending,
        created_at=now,
        attempt_count=0,
        oracle_text_checksum=checksum,
    )
    session.add(job)
