import json
import time
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mtg_evaluator.config import settings
from mtg_evaluator.db.models import Card, EDHRecCardStats

_BASE_URL = "https://json.edhrec.com/pages/"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://edhrec.com/",
}


def _fetch_page(client: httpx.Client, url: str) -> dict:
    resp = client.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _extract_cardviews_and_more(data: dict) -> tuple[list[dict], str | None]:
    """
    Returns (cardviews, next_page_path).
    Handles two formats:
      - Standard EDHREC page: data["container"]["json_dict"]["cardlists"][0]["cardviews"]
      - Browser-console merged export: data["cardviews"] (flat list, no pagination)
    """
    # Browser-console merged export format
    if "cardviews" in data and isinstance(data["cardviews"], list):
        return data["cardviews"], None

    # Standard EDHREC CDN format
    try:
        cardlist_entry = data["container"]["json_dict"]["cardlists"][0]
        cardviews = cardlist_entry.get("cardviews", [])
        more = cardlist_entry.get("more")
        return cardviews, more
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            f"Unexpected EDHREC JSON structure: {exc}. "
            f"Top-level keys: {list(data.keys())}"
        ) from exc


def _collect_all_cards(
    first_data: dict,
    top_n: int,
    client: httpx.Client | None = None,
) -> list[dict]:
    """
    Walk paginated EDHREC results until we have top_n cards or run out of pages.
    If client is None, pagination is skipped (file-only mode).
    """
    all_cards: list[dict] = []
    cardviews, more = _extract_cardviews_and_more(first_data)
    all_cards.extend(cardviews)

    while more and len(all_cards) < top_n and client is not None:
        next_url = _BASE_URL + more
        print(f"  Fetching next page: {next_url} ({len(all_cards)} cards so far)")
        time.sleep(1)
        data = _fetch_page(client, next_url)
        cardviews, more = _extract_cardviews_and_more(data)
        if not cardviews:
            break
        all_cards.extend(cardviews)

    return all_cards


def _name_to_oracle_id(session: Session, names: list[str]) -> dict[str, str]:
    rows = session.execute(
        select(Card.name, Card.oracle_id).where(Card.name.in_(names))
    ).all()
    return {row.name: row.oracle_id for row in rows}


def run_edhrec_ingestion(
    session: Session,
    json_file: Path | None = None,
    save_json: Path | None = None,
) -> int:
    """
    Fetch EDHREC top cards (paginated), match to oracle_ids, upsert edhrec_card_stats.
    Pass json_file to load the first page from a locally saved response.
    Pass save_json to write the first-page raw response to disk for inspection.
    Returns number of rows upserted.
    """
    top_n = settings.edhrec_top_n

    if json_file:
        print(
            f"Loading EDHREC data from {json_file} (pagination disabled in file mode) ..."
        )
        first_data = json.loads(json_file.read_text(encoding="utf-8"))
        all_cards = _collect_all_cards(first_data, top_n, client=None)
    else:
        url = settings.edhrec_top_url
        print(f"Fetching EDHREC top cards from {url} ...")
        with httpx.Client(headers=_HEADERS) as client:
            first_data = _fetch_page(client, url)
            if save_json:
                save_json.write_text(json.dumps(first_data, indent=2), encoding="utf-8")
                print(f"Raw first-page JSON saved to {save_json}")
            all_cards = _collect_all_cards(first_data, top_n, client=client)

    print(f"Collected {len(all_cards)} cards from EDHREC.")

    # Sort by num_decks descending, take top N
    all_cards.sort(key=lambda c: c.get("num_decks", 0), reverse=True)
    all_cards = all_cards[:top_n]

    names = [c["name"] for c in all_cards if c.get("name")]
    name_map = _name_to_oracle_id(session, names)

    # Some EDHREC names use " // " for split/DFC cards
    unmatched = [n for n in names if n not in name_map]
    if unmatched:
        front_names = [n.split(" // ")[0] for n in unmatched]
        front_map = _name_to_oracle_id(session, front_names)
        for original, front in zip(unmatched, front_names):
            if front in front_map:
                name_map[original] = front_map[front]

    now = datetime.utcnow()
    rows = []
    for rank, card in enumerate(all_cards, start=1):
        name = card.get("name", "")
        oracle_id = name_map.get(name)
        if not oracle_id:
            continue
        rows.append(
            {
                "oracle_id": oracle_id,
                "card_name": name,
                "num_decks": card.get("num_decks", 0),
                "rank": rank,
                "fetched_at": now,
            }
        )

    if not rows:
        print("No cards matched. Verify EDHREC URL or card name format.")
        return 0

    stmt = pg_insert(EDHRecCardStats).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["oracle_id"],
        set_={
            "card_name": stmt.excluded.card_name,
            "num_decks": stmt.excluded.num_decks,
            "rank": stmt.excluded.rank,
            "fetched_at": stmt.excluded.fetched_at,
        },
    )
    session.execute(stmt)
    session.flush()

    matched = len(rows)
    skipped = len(all_cards) - matched
    print(
        f"Upserted {matched} cards into edhrec_card_stats. ({skipped} names unmatched)"
    )
    return matched
