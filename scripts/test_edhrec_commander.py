"""Quick live check of the EDHREC commander page fetcher."""

from mtg_evaluator.ingestion.edhrec_commander import commander_slug, fetch_commander_page

slug = commander_slug('Henzie "Toolbox" Torre')
print("slug:", slug)
stats = fetch_commander_page(slug)
print("cards:", len(stats))
for s in stats[:5]:
    print(f"  {s.card_name}: incl={s.inclusion_rate:.0%} synergy={s.synergy} cat={s.category}")
