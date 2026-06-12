"""
Benchmark the deterministic solver against EDHREC consensus for N commanders.

For each commander: build an optimized-style pool + skeleton at the given
bracket, compare against the commander's EDHREC consensus core (>=40%
inclusion), and report overlap, miss reasons, mana base shape, and role fill.

Usage:  uv run python scripts/benchmark_edhrec.py
Writes: benchmark_results.json next to this script.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from sqlalchemy import select

from mtg_evaluator.db.connection import get_session
from mtg_evaluator.db.models import Card, CardClassification
from mtg_evaluator.deckbuilding.request import DeckRequest
from mtg_evaluator.deckbuilding.pool import build_card_pool
from mtg_evaluator.deckbuilding.skeleton import build_skeleton, BASIC_LAND_NAMES
from mtg_evaluator.deckbuilding.edhrec_compare import (
    CONSENSUS_THRESHOLD,
    compare_to_edhrec,
)

COMMANDERS: list[tuple[str, int]] = [
    ("Krenko, Mob Boss", 4),
    ('Henzie "Toolbox" Torre', 4),
    ("Hearthhull, the Worldseed", 4),
    ("Atraxa, Praetors' Voice", 3),
    ("Meren of Clan Nel Toth", 3),
    ("The Ur-Dragon", 3),
    ("Yuriko, the Tiger's Shadow", 4),
    ("Niv-Mizzet, Parun", 3),
    ("Wilhelt, the Rotcleaver", 2),
    ("Giada, Font of Hope", 2),
]


def benchmark_one(name: str, bracket: int) -> dict:
    t0 = time.time()
    req = DeckRequest(
        commander_name=name,
        bracket=bracket,
        archetype=None,  # auto-detect from profile
        build_style="optimized",
    )
    with get_session() as session:
        pool = build_card_pool(session, req)
    skel = build_skeleton(pool)
    deck_names = skel.all_cards

    # classified_names exactly as app.py builds it
    deck_set = set(deck_names)
    miss_names = [
        n
        for n, s in pool.commander_stats.items()
        if (s.get("inclusion_rate") or 0) >= CONSENSUS_THRESHOLD and n not in deck_set
    ]
    classified_names: set[str] = set()
    if miss_names:
        with get_session() as s2:
            classified_names = set(
                s2.scalars(
                    select(Card.name)
                    .join(
                        CardClassification,
                        CardClassification.oracle_id == Card.oracle_id,
                    )
                    .where(Card.name.in_(miss_names))
                    .where(CardClassification.is_valid == True)  # noqa: E712
                )
            )

    cmp = compare_to_edhrec(deck_names, pool, pool.commander_stats, classified_names)

    basics = [c for c in skel.lands if c in BASIC_LAND_NAMES]
    reasons: dict[str, int] = {}
    if cmp:
        for m in cmp.missed:
            reasons[m.reason] = reasons.get(m.reason, 0) + 1

    result = {
        "commander": name,
        "bracket": bracket,
        "archetype": pool.archetype,
        "pool_size": pool.total,
        "deck_size": skel.total,
        "lands": len(skel.lands),
        "basics": len(basics),
        "role_fill": skel.role_fill,
        "role_targets": pool.role_targets.to_dict() if pool.role_targets else {},
        "overlap_pct": cmp.overlap_pct if cmp else None,
        "consensus_size": cmp.consensus_size if cmp else 0,
        "matched": len(cmp.matched) if cmp else 0,
        "miss_reasons": reasons,
        "missed": [m.to_dict() for m in cmp.missed] if cmp else [],
        "off_meta": [p.to_dict() for p in (cmp.off_meta if cmp else [])][:10],
        "deck": {
            "lands": skel.lands,
            "ramp": skel.ramp,
            "draw": skel.draw,
            "removal": skel.removal,
            "synergy": skel.synergy,
            "combo": skel.combo,
            "other": skel.other,
        },
        "seconds": round(time.time() - t0, 1),
    }
    return result


def main() -> None:
    results = []
    for name, bracket in COMMANDERS:
        print(f"=== {name} (B{bracket}) ===", flush=True)
        try:
            r = benchmark_one(name, bracket)
        except Exception as exc:
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            results.append({"commander": name, "bracket": bracket, "error": str(exc)})
            continue
        ov = r["overlap_pct"]
        print(
            f"  arch={r['archetype']}  overlap={ov if ov is not None else 'n/a'}"
            f"  ({r['matched']}/{r['consensus_size']})  lands={r['lands']}"
            f" (basics={r['basics']})  misses={r['miss_reasons']}  {r['seconds']}s"
        )
        results.append(r)

    out = Path(__file__).parent / "benchmark_results.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
