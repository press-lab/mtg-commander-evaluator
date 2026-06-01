from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mtg_evaluator.db.models import CardIngestionRun, RawScryfallCard, IngestionStatus
from mtg_evaluator.ingestion.scryfall import (
    compute_oracle_text_checksum,
    fetch_bulk_metadata,
    download_bulk_file,
    load_cards_from_file,
    get_latest_cached_bulk_file,
)


def run_ingestion(session: Session, use_cache: bool = False) -> CardIngestionRun:
    metadata = fetch_bulk_metadata()
    download_url = metadata["download_uri"]

    if use_cache:
        cached = get_latest_cached_bulk_file()
        if cached:
            print(f"Using cached file: {cached}")
            bulk_path = cached
        else:
            print("No cached file found, downloading.")
            bulk_path = download_bulk_file(download_url)
    else:
        bulk_path = download_bulk_file(download_url)

    run = CardIngestionRun(
        started_at=datetime.utcnow(),
        source_url=download_url,
        bulk_type="oracle_cards",
        status=IngestionStatus.running,
    )
    session.add(run)
    session.flush()

    try:
        cards = load_cards_from_file(bulk_path)
        _store_raw_cards(session, run, cards)
        run.card_count = len(cards)
        run.status = IngestionStatus.completed
        run.completed_at = datetime.utcnow()
        print(f"Ingestion complete: {len(cards)} cards stored.")
    except Exception as exc:
        run.status = IngestionStatus.failed
        run.error_message = str(exc)
        run.completed_at = datetime.utcnow()
        raise

    return run


def _store_raw_cards(
    session: Session, run: CardIngestionRun, cards: list[dict]
) -> None:
    batch_size = 500
    now = datetime.utcnow()

    for i in range(0, len(cards), batch_size):
        batch = cards[i : i + batch_size]
        rows = [
            {
                "oracle_id": card["oracle_id"],
                "scryfall_id": card["id"],
                "ingestion_run_id": run.id,
                "raw_json": card,
                "ingested_at": now,
                "oracle_text_checksum": compute_oracle_text_checksum(card),
            }
            for card in batch
            if "oracle_id" in card
        ]

        if rows:
            stmt = pg_insert(RawScryfallCard).values(rows)
            stmt = stmt.on_conflict_do_nothing()
            session.execute(stmt)

        if i % 5000 == 0:
            print(f"  Stored {i + len(batch)}/{len(cards)} raw cards...")
            session.flush()
