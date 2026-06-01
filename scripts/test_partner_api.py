import httpx

search = httpx.get("http://127.0.0.1:8000/api/search/commanders?q=tymna", timeout=10)
tymna = search.json()[0]
oid = tymna["oracle_id"]
print(f"Tymna: {tymna['name']} ({oid})")

resp = httpx.get(f"http://127.0.0.1:8000/api/partners/{oid}", timeout=10)
data = resp.json()
print(f"Partner type: {data['partner_info']['type']}")
print(f"First 5 partners: {[p['name'] for p in data['valid_partners'][:5]]}")

info = httpx.get(f"http://127.0.0.1:8000/api/commander-info/{oid}", timeout=10).json()
print(f"Archetypes: {[a['archetype'] for a in info['archetypes'][:3]]}")
print(f"Partner label: {info['partner_info']['label'] if info['partner_info'] else 'none'}")

# Test Shadowheart (Choose a Background)
sh = httpx.get("http://127.0.0.1:8000/api/search/commanders?q=shadowheart", timeout=10).json()
if sh:
    sh_info = httpx.get(f"http://127.0.0.1:8000/api/commander-info/{sh[0]['oracle_id']}", timeout=10).json()
    sh_partners = httpx.get(f"http://127.0.0.1:8000/api/partners/{sh[0]['oracle_id']}", timeout=10).json()
    print(f"\nShadowheart partner type: {sh_info['partner_info']['type']}")
    print(f"Background options (first 5): {[p['name'] for p in sh_partners['valid_partners'][:5]]}")
