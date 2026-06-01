from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mtg_evaluator.db.models import (
    Card, CardLegality, CardFace, CardKeyword, RawScryfallCard, CardIngestionRun, IngestionStatus,
)
from mtg_evaluator.ingestion.scryfall import compute_oracle_text_checksum

BATCH_SIZE = 500


def normalize_all(session: Session, run_id: str | None = None) -> int:
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

    now = datetime.utcnow()

    existing_oracle_ids = set(
        row[0] for row in session.execute(select(Card.oracle_id)).all()
    )

    # Pass 1: upsert all cards in batches
    card_data_by_oracle_id: dict[str, dict] = {}
    for raw in raw_cards:
        card_json = raw.raw_json
        oracle_id = card_json.get("oracle_id")
        if not oracle_id:
            continue
        checksum = compute_oracle_text_checksum(card_json)
        type_line = card_json.get("type_line", "")

        first_seen = now if oracle_id not in existing_oracle_ids else None
        card_data_by_oracle_id[oracle_id] = _build_card(card_json, oracle_id, checksum, type_line, now, first_seen)

    card_rows = list(card_data_by_oracle_id.values())
    for i in range(0, len(card_rows), BATCH_SIZE):
        batch = card_rows[i : i + BATCH_SIZE]
        stmt = pg_insert(Card).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["oracle_id"],
            set_={
                col: stmt.excluded[col]
                for col in [
                    "scryfall_id", "name", "mana_cost", "cmc", "type_line", "oracle_text",
                    "colors", "color_identity", "power", "toughness", "loyalty",
                    "is_legendary", "is_creature", "is_planeswalker", "is_land",
                    "is_instant", "is_sorcery", "layout", "rarity", "set_code",
                    "scryfall_uri", "image_uri", "oracle_text_checksum", "updated_at",
                ]
            },
        )
        session.execute(stmt)
        if i % 5000 == 0:
            print(f"  Cards upserted: {min(i + BATCH_SIZE, len(card_rows))}/{len(card_rows)}")
            session.flush()

    session.flush()
    print(f"Pass 1 complete: {len(card_rows)} cards upserted.")

    # Pass 2: upsert legalities, faces, keywords in batches
    legality_rows: list[dict] = []
    face_rows: list[dict] = []
    keyword_rows: list[dict] = []

    for raw in raw_cards:
        card_json = raw.raw_json
        oracle_id = card_json.get("oracle_id")
        if not oracle_id:
            continue

        for fmt, status in card_json.get("legalities", {}).items():
            legality_rows.append({"oracle_id": oracle_id, "format": fmt, "status": status})

        for idx, face in enumerate(card_json.get("card_faces", [])):
            face_rows.append({
                "oracle_id": oracle_id,
                "face_index": idx,
                "name": face.get("name", ""),
                "mana_cost": face.get("mana_cost"),
                "type_line": face.get("type_line"),
                "oracle_text": face.get("oracle_text"),
                "power": face.get("power"),
                "toughness": face.get("toughness"),
                "loyalty": face.get("loyalty"),
                "colors": sorted(face.get("colors", [])),
            })

        for kw in card_json.get("keywords", []):
            keyword_rows.append({"oracle_id": oracle_id, "keyword": kw})

    for i in range(0, len(legality_rows), BATCH_SIZE):
        batch = legality_rows[i : i + BATCH_SIZE]
        stmt = pg_insert(CardLegality).values(batch)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_card_legalities_oracle_format",
            set_={"status": stmt.excluded.status},
        )
        session.execute(stmt)
    session.flush()
    print(f"Pass 2: {len(legality_rows)} legalities upserted.")

    for i in range(0, len(face_rows), BATCH_SIZE):
        batch = face_rows[i : i + BATCH_SIZE]
        stmt = pg_insert(CardFace).values(batch)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_card_faces_oracle_face",
            set_={
                "name": stmt.excluded.name,
                "mana_cost": stmt.excluded.mana_cost,
                "type_line": stmt.excluded.type_line,
                "oracle_text": stmt.excluded.oracle_text,
                "power": stmt.excluded.power,
                "toughness": stmt.excluded.toughness,
                "loyalty": stmt.excluded.loyalty,
                "colors": stmt.excluded.colors,
            },
        )
        session.execute(stmt)
    session.flush()
    print(f"Pass 2: {len(face_rows)} faces upserted.")

    if keyword_rows:
        for i in range(0, len(keyword_rows), BATCH_SIZE):
            batch = keyword_rows[i : i + BATCH_SIZE]
            stmt = pg_insert(CardKeyword).values(batch)
            stmt = stmt.on_conflict_do_nothing(constraint="uq_card_keywords_oracle_keyword")
            session.execute(stmt)
        session.flush()
    print(f"Pass 2: {len(keyword_rows)} keywords upserted.")

    print(f"Normalization complete: {len(card_rows)} cards processed.")
    return len(card_rows)


def _build_card(card_json: dict, oracle_id: str, checksum: str, type_line: str, now: datetime, first_seen: datetime | None) -> dict:
    image_uris = card_json.get("image_uris", {})
    image_uri = image_uris.get("normal") or image_uris.get("large") or image_uris.get("png")

    if not image_uri and card_json.get("card_faces"):
        face_uris = card_json["card_faces"][0].get("image_uris", {})
        image_uri = face_uris.get("normal") or face_uris.get("large")

    tl_lower = type_line.lower()
    return {
        "oracle_id": oracle_id,
        "scryfall_id": card_json["id"],
        "name": card_json.get("name", ""),
        "mana_cost": card_json.get("mana_cost"),
        "cmc": float(card_json.get("cmc", 0)),
        "type_line": type_line,
        "oracle_text": card_json.get("oracle_text"),
        "colors": sorted(card_json.get("colors", [])),
        "color_identity": sorted(card_json.get("color_identity", [])),
        "power": card_json.get("power"),
        "toughness": card_json.get("toughness"),
        "loyalty": card_json.get("loyalty"),
        "is_legendary": "legendary" in tl_lower,
        "is_creature": "creature" in tl_lower,
        "is_planeswalker": "planeswalker" in tl_lower,
        "is_land": "land" in tl_lower,
        "is_instant": "instant" in tl_lower,
        "is_sorcery": "sorcery" in tl_lower,
        "layout": card_json.get("layout", "normal"),
        "rarity": card_json.get("rarity", "common"),
        "set_code": card_json.get("set", ""),
        "scryfall_uri": card_json.get("scryfall_uri", ""),
        "image_uri": image_uri,
        "oracle_text_checksum": checksum,
        "first_seen_at": first_seen or now,
        "updated_at": now,
    }
