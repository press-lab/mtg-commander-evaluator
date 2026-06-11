"""End-to-end check: budget filter, salt filter, per-commander synergy in pool build."""

from mtg_evaluator.db.connection import get_session
from mtg_evaluator.deckbuilding.pool import build_card_pool
from mtg_evaluator.deckbuilding.request import DeckRequest

with get_session() as session:
    req = DeckRequest(
        commander_name='Henzie "Toolbox" Torre',
        bracket=3,
        archetype="midrange",
        want_combos=False,
        max_card_price=5.0,
        salt_tolerance="medium",
        pool_size=120,
    )
    pool = build_card_pool(session, req)
    session.commit()  # persist fetched commander stats

    cards = pool.all_cards
    print(f"pool total: {pool.total}")

    over_budget = [c for c in cards if c.price_usd is not None and c.price_usd > 5.0]
    print(f"cards over $5: {len(over_budget)} (expect 0)")

    salty = [c for c in cards if c.salt_score is not None and c.salt_score > 2.0]
    print(f"cards saltier than 2.0: {len(salty)} (expect 0)")

    with_synergy = [c for c in cards if c.commander_inclusion is not None]
    print(f"cards with per-commander data: {len(with_synergy)}")
    for c in sorted(with_synergy, key=lambda x: x.commander_inclusion or 0, reverse=True)[:5]:
        print(
            f"  {c.name}: score={c.score} incl={c.commander_inclusion:.0%} "
            f"synergy={c.commander_synergy} price=${c.price_usd}"
        )
