"""Debug per-commander stats fetch inside a session."""

import traceback

from sqlalchemy import select

from mtg_evaluator.db.connection import get_session
from mtg_evaluator.db.models import Card
from mtg_evaluator.ingestion.edhrec_commander import get_or_fetch_commander_stats

with get_session() as session:
    commander = session.scalars(
        select(Card).where(Card.name == 'Henzie "Toolbox" Torre')
    ).first()
    print("commander:", commander.name, commander.oracle_id)
    try:
        stats = get_or_fetch_commander_stats(session, commander)
        print("stats:", len(stats))
        for name in list(stats)[:3]:
            print(" ", name, stats[name])
        session.commit()
    except Exception:
        traceback.print_exc()
