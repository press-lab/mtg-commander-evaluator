"""
Per-commander EDHREC inclusion data.

Fetches https://json.edhrec.com/pages/commanders/<slug>.json for a specific
commander and stores per-card inclusion rates + synergy scores in
edhrec_commander_stats. Lazily fetched on first pool build (same pattern as
commander_profiles) and refreshed when older than the cache window.

synergy = inclusion in this commander's decks minus inclusion in other decks
of the same color identity. It is THE per-commander affinity signal — far
stronger than global popularity.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mtg_evaluator.config import settings
from mtg_evaluator.db.models import Card, EDHRecCommanderStats

# Plain GET with a minimal UA works reliably against the EDHREC CDN;
# heavier browser-mimicking headers trigger TLS-level resets.
_HEADERS = {"User-Agent": "Mozilla/5.0"}

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9\s-]")
_SLUG_SPACE_RE = re.compile(r"[\s_]+")


@dataclass
class CommanderCardStat:
    card_name: str
    num_decks: int
    potential_decks: int
    inclusion_rate: float  # 0-1
    synergy: float | None  # can be negative
    category: str | None


def commander_slug(name: str, partner_name: str | None = None) -> str:
    """
    Convert a commander name to an EDHREC URL slug.
    'Henzie "Toolbox" Torre' -> 'henzie-toolbox-torre'
    Partner pairs are joined: 'akiri-line-slinger-silas-renn-seeker-adept'
    """

    def _one(n: str) -> str:
        # DFC names use only the front face
        n = n.split(" // ")[0]
        n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode()
        n = _SLUG_STRIP_RE.sub("", n.lower())
        return _SLUG_SPACE_RE.sub("-", n).strip("-")

    slug = _one(name)
    if partner_name:
        slug = f"{slug}-{_one(partner_name)}"
    return slug


def _parse_commander_page(data: dict) -> list[CommanderCardStat]:
    """Extract all cardviews across the page's cardlists."""
    stats: list[CommanderCardStat] = []
    seen: set[str] = set()
    try:
        cardlists = data["container"]["json_dict"]["cardlists"]
    except (KeyError, TypeError):
        return stats

    for cardlist in cardlists or []:
        category = cardlist.get("tag") or cardlist.get("header")
        for cv in cardlist.get("cardviews", []):
            name = cv.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            num = int(cv.get("num_decks") or 0)
            potential = int(cv.get("potential_decks") or 0)
            inclusion = round(num / potential, 4) if potential > 0 else 0.0
            synergy = cv.get("synergy")
            stats.append(
                CommanderCardStat(
                    card_name=name,
                    num_decks=num,
                    potential_decks=potential,
                    inclusion_rate=inclusion,
                    synergy=round(float(synergy), 4) if synergy is not None else None,
                    category=category,
                )
            )
    return stats


def fetch_commander_page(slug: str) -> list[CommanderCardStat]:
    """Fetch and parse one commander page. Raises on network failure."""
    url = settings.edhrec_commander_url.format(slug=slug)
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = httpx.get(url, timeout=30, headers=_HEADERS)
            resp.raise_for_status()
            return _parse_commander_page(resp.json())
        except httpx.HTTPStatusError:
            raise  # 404 = unknown slug; retrying won't help
        except Exception as exc:
            last_exc = exc
            time.sleep(1 + attempt)
    raise last_exc  # type: ignore[misc]


def _load_cached(
    session: Session, commander_oracle_id: str
) -> list[EDHRecCommanderStats]:
    return list(
        session.scalars(
            select(EDHRecCommanderStats).where(
                EDHRecCommanderStats.commander_oracle_id == commander_oracle_id
            )
        )
    )


def get_or_fetch_commander_stats(
    session: Session,
    commander: Card,
    partner: Card | None = None,
    *,
    force_refresh: bool = False,
) -> dict[str, dict]:
    """
    Return {card_name: {inclusion_rate, synergy, num_decks}} for a commander,
    fetching from EDHREC and caching in DB when missing or stale.
    Returns {} when EDHREC is unreachable and no cache exists — callers must
    treat per-commander data as an optional signal.
    """
    cached = _load_cached(session, commander.oracle_id)
    max_age = timedelta(days=settings.edhrec_commander_cache_days)
    fresh = cached and all(
        datetime.utcnow() - row.fetched_at < max_age for row in cached[:1]
    )

    if cached and fresh and not force_refresh:
        return {
            row.card_name: {
                "inclusion_rate": float(row.inclusion_rate),
                "synergy": float(row.synergy) if row.synergy is not None else None,
                "num_decks": row.num_decks,
            }
            for row in cached
        }

    slug = commander_slug(commander.name, partner.name if partner else None)
    try:
        stats = fetch_commander_page(slug)
    except Exception:
        # Stale cache beats nothing; nothing beats a crashed build.
        if cached:
            return {
                row.card_name: {
                    "inclusion_rate": float(row.inclusion_rate),
                    "synergy": float(row.synergy) if row.synergy is not None else None,
                    "num_decks": row.num_decks,
                }
                for row in cached
            }
        return {}

    if not stats:
        return {}

    # Resolve card names to oracle_ids (front-face fallback for DFCs)
    names = [s.card_name for s in stats]
    rows = session.execute(
        select(Card.name, Card.oracle_id).where(Card.name.in_(names))
    ).all()
    name_map = {r.name: r.oracle_id for r in rows}
    unmatched = [n for n in names if n not in name_map]
    if unmatched:
        front_names = [n.split(" // ")[0] for n in unmatched]
        front_rows = session.execute(
            select(Card.name, Card.oracle_id).where(Card.name.in_(front_names))
        ).all()
        front_map = {r.name: r.oracle_id for r in front_rows}
        for original, front in zip(unmatched, front_names):
            if front in front_map:
                name_map[original] = front_map[front]

    now = datetime.utcnow()
    session.execute(
        delete(EDHRecCommanderStats).where(
            EDHRecCommanderStats.commander_oracle_id == commander.oracle_id
        )
    )
    for s in stats:
        session.add(
            EDHRecCommanderStats(
                commander_oracle_id=commander.oracle_id,
                card_name=s.card_name,
                card_oracle_id=name_map.get(s.card_name),
                num_decks=s.num_decks,
                potential_decks=s.potential_decks,
                inclusion_rate=s.inclusion_rate,
                synergy=s.synergy,
                category=s.category,
                fetched_at=now,
            )
        )
    session.flush()

    return {
        s.card_name: {
            "inclusion_rate": s.inclusion_rate,
            "synergy": s.synergy,
            "num_decks": s.num_decks,
        }
        for s in stats
    }
