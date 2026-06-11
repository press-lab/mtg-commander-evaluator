"""Smoke test: /api/evaluate with the new recommendation engine."""

from fastapi.testclient import TestClient

from mtg_evaluator.api.app import app

client = TestClient(app)

DECK = """\
1 Henzie "Toolbox" Torre *CMDR*
1 Sol Ring
1 Arcane Signet
1 Solemn Simulacrum
1 Beast Whisperer
1 Protean Hulk
1 Etali, Primal Storm
1 Terror of the Peaks
1 Rampaging Brontodon
1 Thran Dynamo
1 Cultivate
1 Kodama's Reach
1 Blasphemous Act
1 Beast Within
1 Chaos Warp
1 Deathsprout
1 Goremand
1 Ohran Frostfang
1 Court of Bounty
1 Garruk's Uprising
1 Elemental Bond
1 Mountain
1 Forest
1 Swamp
1 Command Tower
1 Exotic Orchard
"""

r = client.post("/api/evaluate", json={"decklist": DECK, "deck_name": "Smoke Henzie"})
print("evaluate:", r.status_code)
if r.status_code != 200:
    print(r.text[:500])
    raise SystemExit(1)

d = r.json()
print("bracket:", d["bracket_label"], "| score:", d.get("deck_score"))

recs = d.get("recommendations") or {}
staples = recs.get("staples_missing", [])
print(f"\nstaples missing ({len(staples)}):")
for s in staples[:6]:
    print(f"  {s['name']}: {s['reason']}")

gaps = recs.get("gap_fillers", {})
print(f"\ngap fillers ({len(gaps)} roles):")
for role, cards in gaps.items():
    print(f"  {role}: " + ", ".join(c["name"] for c in cards[:3]))

cuts = recs.get("cut_candidates", [])
print(f"\ncut candidates ({len(cuts)}):")
for c in cuts[:5]:
    print(f"  {c['name']}: {c['reason']}")
