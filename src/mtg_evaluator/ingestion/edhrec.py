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


# ---------------------------------------------------------------------------
# Salt scores
# ---------------------------------------------------------------------------

import re as _re

_SALT_LABEL_RE = _re.compile(r"Salt\s*Score:\s*([\d.]+)", _re.IGNORECASE)

# Fallback when the EDHREC salt page is unreachable. Approximate scores from
# recent EDHREC community salt surveys (0-4 scale). Only the saltiest cards
# matter for filtering — everything absent is treated as unsalted.
_CURATED_SALT: dict[str, float] = {
    "Stasis": 3.3, "Winter Orb": 3.3, "Armageddon": 3.2, "Static Orb": 3.1,
    "Nether Void": 3.0, "Tergrid, God of Fright // Tergrid's Lantern": 3.0,
    "Vorinclex, Voice of Hunger": 3.0, "Obliterate": 2.9, "Expropriate": 2.9,
    "Jin-Gitaxias, Core Augur": 2.9, "Ravages of War": 2.9, "Sunder": 2.8,
    "Decree of Annihilation": 2.8, "Grand Arbiter Augustin IV": 2.8,
    "Jokulhaups": 2.8, "Blood Moon": 2.7, "Cyclonic Rift": 2.7,
    "Opposition Agent": 2.7, "Mindslaver": 2.7, "Narset, Parter of Veils": 2.6,
    "Hokori, Dust Drinker": 2.6, "Drannith Magistrate": 2.6, "Humility": 2.6,
    "The Tabernacle at Pendrell Vale": 2.6, "Rule of Law": 2.5,
    "Time Stretch": 2.5, "Smokestack": 2.5, "Contamination": 2.5,
    "Nexus of Fate": 2.5, "Notion Thief": 2.4, "Thoughts of Ruin": 2.4,
    "Back to Basics": 2.4, "Sen Triplets": 2.4, "Possessed Portal": 2.4,
    "Impending Disaster": 2.3, "Stranglehold": 2.3, "Void Winnower": 2.3,
    "Sheoldred, the Apocalypse": 2.3, "Orcish Bowmasters": 2.3,
    "Rhystic Study": 2.2, "Thassa's Oracle": 2.2, "Necropotence": 2.1,
    "Gilded Drake": 2.1, "Counterspell": 1.8, "Sol Ring": 1.6,
    "Smothering Tithe": 1.9, "Dockside Extortionist": 2.2,
    "Fierce Guardianship": 2.0, "Force of Will": 1.9, "Demonic Tutor": 1.7,
}


def _extract_salt(card: dict) -> float | None:
    """Pull a salt score from an EDHREC cardview (field or label text)."""
    if isinstance(card.get("salt"), (int, float)):
        return round(float(card["salt"]), 2)
    label = card.get("label") or ""
    m = _SALT_LABEL_RE.search(label)
    if m:
        try:
            return round(float(m.group(1)), 2)
        except ValueError:
            return None
    return None


def run_salt_ingestion(
    session: Session,
    json_file: Path | None = None,
) -> int:
    """
    Fetch EDHREC salt scores and store them on edhrec_card_stats.salt_score.
    Falls back to the curated list if the remote page is unreachable or
    yields no scores. Returns number of rows updated.
    """
    salt_by_name: dict[str, float] = {}

    try:
        if json_file:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            cards = _collect_all_cards(data, top_n=1000, client=None)
        else:
            url = settings.edhrec_salt_url
            print(f"Fetching EDHREC salt scores from {url} ...")
            with httpx.Client(headers=_HEADERS) as client:
                data = _fetch_page(client, url)
                cards = _collect_all_cards(data, top_n=1000, client=client)
        for card in cards:
            name = card.get("name")
            salt = _extract_salt(card)
            if name and salt is not None:
                salt_by_name[name] = salt
    except Exception as exc:
        print(f"EDHREC salt fetch failed ({exc}); using curated fallback list.")

    if not salt_by_name:
        salt_by_name = dict(_CURATED_SALT)
        print(f"Using curated salt list: {len(salt_by_name)} cards.")
    else:
        print(f"Fetched salt scores for {len(salt_by_name)} cards.")

    name_map = _name_to_oracle_id(session, list(salt_by_name))
    unmatched = [n for n in salt_by_name if n not in name_map]
    if unmatched:
        front_names = [n.split(" // ")[0] for n in unmatched]
        front_map = _name_to_oracle_id(session, front_names)
        for original, front in zip(unmatched, front_names):
            if front in front_map:
                name_map[original] = front_map[front]

    updated = 0
    now = datetime.utcnow()
    for name, salt in salt_by_name.items():
        oracle_id = name_map.get(name)
        if not oracle_id:
            continue
        existing = session.get(EDHRecCardStats, oracle_id)
        if existing:
            existing.salt_score = salt
        else:
            # Salty card outside the top-N popularity list — still store it
            session.add(EDHRecCardStats(
                oracle_id=oracle_id,
                card_name=name,
                num_decks=0,
                rank=0,
                salt_score=salt,
                fetched_at=now,
            ))
        updated += 1

    session.flush()
    print(f"Salt scores stored for {updated} cards.")
    return updated
