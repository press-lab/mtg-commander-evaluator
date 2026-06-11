"""API smoke test: build with budget/salt + card detail endpoint."""

from fastapi.testclient import TestClient

from mtg_evaluator.api.app import app

client = TestClient(app)

# Build with budget + salt
r = client.post(
    "/api/build",
    json={
        "commander_name": 'Henzie "Toolbox" Torre',
        "bracket": 3,
        "archetype": "midrange",
        "want_combos": False,
        "max_card_price": 5.0,
        "salt_tolerance": "medium",
        "pool_size": 60,
    },
)
print("build:", r.status_code)
data = r.json()
print("  total:", data["total"])
print("  profile:", bool(data["commander_profile"]))
core = data["core"][:3]
for c in core:
    print(
        f"  {c['name']}: ${c['price_usd']} incl={c['commander_inclusion']} "
        f"salt={c['salt_score']}"
    )

# Card detail
oid = core[0]["oracle_id"]
r2 = client.get(f"/api/card/{oid}")
print("card detail:", r2.status_code)
d = r2.json()
print(f"  {d['name']}: functions={d['functions'][:4]} combos={d['combo_count']}")
