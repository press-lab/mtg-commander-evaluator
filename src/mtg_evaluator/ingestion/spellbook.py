"""
Ingest Commander Spellbook combo data into spellbook_combos / spellbook_combo_cards.

BracketTag values (from commanderspellbook.com/syntax-guide/):
  R = Ruthless   → WotC B4+  (fast infinite combos — hard B4 floor)
  S = Spicy      → ~B3-4     (powerful, needs setup — hard B3 floor)
  P = Powerful   → B3+       (two-card combos w/ game changers — B3 floor)
  O = Oddball    → ~B2-3     (informational only)
  C = Core       → B2        (informational only)
  E = Exhibition → B1        (informational only)
  B = Banned     → skip (illegal)

We store ALL non-banned combos so deck building recommendations can surface
any combo available to a given commander within bracket constraints.

API: https://backend.commanderspellbook.com/variants/?commander_legal=true
Paginated (limit/offset). count=null so we paginate until next=null.
Rate limit: 2s between pages keeps us safe (~30 pages for ~12k combos = ~60s).
Resumable: uses upsert so a partial run can be re-run without duplicates.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from mtg_evaluator.db.models import Card, SpellbookCombo, SpellbookComboCard

_BASE_URL = "https://backend.commanderspellbook.com/variants/"
_PAGE_SIZE = 400
_RATE_LIMIT_S = 2.0  # seconds between pages — keeps us under their rate limit


@dataclass
class SpellbookIngestionResult:
    combos_upserted: int
    combo_cards_inserted: int
    pages_fetched: int


def _fetch_page(client: httpx.Client, url: str) -> dict:
    for attempt in range(6):
        resp = client.get(url, timeout=30)
        if resp.status_code == 429:
            wait = 10 * (2**attempt)  # 10, 20, 40, 80, 160, 320s
            print(
                f"  Rate limited (attempt {attempt + 1}), waiting {wait}s...",
                flush=True,
            )
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Failed to fetch {url} after 6 attempts")


def _results_description(variant: dict) -> str | None:
    """Short human-readable combo result string."""
    produces = variant.get("produces", [])
    effects = [
        p.get("feature", {}).get("name", "") for p in produces if p.get("feature")
    ]
    return "; ".join(e for e in effects if e) or None


def run_spellbook_ingestion(session: Session) -> SpellbookIngestionResult:
    now = datetime.utcnow()
    result = SpellbookIngestionResult(
        combos_upserted=0, combo_cards_inserted=0, pages_fetched=0
    )

    # Build oracle_id lookup from our card DB (name → oracle_id)
    rows = session.execute(select(Card.name, Card.oracle_id)).all()
    name_to_oracle: dict[str, str] = {row.name: row.oracle_id for row in rows}
    front_map: dict[str, str] = {
        name.split(" // ")[0]: oid
        for name, oid in name_to_oracle.items()
        if " // " in name
    }

    def resolve(card_name: str) -> str | None:
        return name_to_oracle.get(card_name) or front_map.get(card_name)

    # Clear + re-insert approach — safe because we commit the clear first,
    # then commit progress every COMMIT_EVERY pages so a crash/rate-limit
    # doesn't wipe out all progress.
    print("  Clearing existing combo data...", flush=True)
    session.execute(text("DELETE FROM spellbook_combo_cards"))
    session.execute(text("DELETE FROM spellbook_combos"))
    session.commit()  # commit the clear so a crash doesn't roll back pages of work

    with httpx.Client(headers={"User-Agent": "mtg-evaluator/1.0 (research)"}) as client:
        next_url: str | None = (
            f"{_BASE_URL}?commander_legal=true&limit={_PAGE_SIZE}&offset=0"
        )
        print(
            "  Fetching all combos (2s/page, ~3-5 min for full dataset)...", flush=True
        )

        while next_url:
            data = _fetch_page(client, next_url)
            result.pages_fetched += 1
            variants = data.get("results", [])

            for variant in variants:
                vtag = variant.get("bracketTag")
                if vtag == "B":
                    continue  # banned card in combo — skip

                uses = variant.get("uses", [])
                card_entries = [u["card"] for u in uses if u.get("card")]
                card_count = len(card_entries)
                if card_count == 0:
                    continue

                session.add(
                    SpellbookCombo(
                        spellbook_id=str(variant["id"]),
                        card_count=card_count,
                        bracket_tag=vtag,
                        is_commander_legal=True,
                        results_description=_results_description(variant),
                        fetched_at=now,
                    )
                )
                result.combos_upserted += 1

                for card_data in card_entries:
                    name = card_data.get("name", "")
                    oracle_id = card_data.get("oracleId") or resolve(name) or None
                    session.add(
                        SpellbookComboCard(
                            combo_id=str(variant["id"]),
                            oracle_id=oracle_id,
                            card_name=name,
                        )
                    )
                    result.combo_cards_inserted += 1

            if result.pages_fetched % 5 == 0:
                session.commit()  # durably save progress every 5 pages
                print(
                    f"    page {result.pages_fetched} — {result.combos_upserted} combos stored",
                    flush=True,
                )

            next_url = data.get("next")
            if next_url:
                time.sleep(_RATE_LIMIT_S)

    session.commit()
    return result
