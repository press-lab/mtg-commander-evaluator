"""Build 'optimized' decks and compare against EDHREC consensus — the receipts."""

import sys

from fastapi.testclient import TestClient

from mtg_evaluator.api.app import app

client = TestClient(app)

COMMANDERS = sys.argv[1:] or ['Henzie "Toolbox" Torre', "Krenko, Mob Boss"]

for name in COMMANDERS:
    print("=" * 70)
    print(f"COMMANDER: {name}  (bracket 4, style: optimized)")
    print("=" * 70)
    r = client.post(
        "/api/build",
        json={
            "commander_name": name,
            "bracket": 4,
            "want_combos": True,
            "build_style": "optimized",
            "pool_size": 200,
            "assemble": True,
        },
    )
    if r.status_code != 200:
        print("BUILD FAILED:", r.status_code, r.text[:300])
        continue
    d = r.json()
    a = d.get("assembled") or {}
    if a.get("error"):
        print("ASSEMBLY ERROR:", a["error"])
        continue

    print(f"archetype: {d['archetype']} | total: {a['total']} cards")
    print(f"validation warnings: {a['validation_warnings'] or 'none'}")
    print(f"llm swaps: {len(a.get('llm_swaps') or [])}")
    for s in (a.get("llm_swaps") or [])[:5]:
        print(f"  {s['remove']} -> {s['add']}: {s['reason'][:70]}")
    cons = a.get("consistency") or {}
    mana = a.get("mana_analysis") or {}
    print(
        f"consistency: {cons.get('grade')} ({cons.get('overall_score')}) | "
        f"cast reliability: {mana.get('cast_reliability')}"
    )

    cmp = a.get("edhrec_comparison")
    if not cmp:
        print("NO EDHREC COMPARISON AVAILABLE")
        continue
    print(
        f"\nEDHREC: {cmp['overlap_pct']:.0%} overlap with "
        f"{cmp['consensus_size']} consensus cards (>=40% inclusion)"
    )
    print(f"\nMISSED CONSENSUS ({len(cmp['missed'])}):")
    for m in cmp["missed"]:
        print(f"  {m['name']} ({m['inclusion_rate']:.0%}) [{m['reason']}] {m['detail'][:60]}")
    print(f"\nOFF-META PICKS ({len(cmp['off_meta'])}):")
    for p in cmp["off_meta"][:8]:
        incl = f"{p['inclusion_rate']:.0%}" if p["inclusion_rate"] is not None else "n/a"
        why = (p.get("rationale") or "")[:55]
        print(f"  {p['name']} (edhrec {incl}, score {p['score']}) {why}")
    print()
