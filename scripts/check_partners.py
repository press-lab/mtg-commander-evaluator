from mtg_evaluator.db.connection import get_session
from sqlalchemy import text

with get_session() as s:
    rows = s.execute(text(r"""
        SELECT
          CASE
            WHEN oracle_text ILIKE '%partner with %' THEN 'partner_with'
            WHEN oracle_text ~* E'(^|\n)partner($|\n|\.| )' THEN 'generic_partner'
            WHEN oracle_text ILIKE '%choose a background%' THEN 'choose_background'
            WHEN oracle_text ILIKE '%friends forever%' THEN 'friends_forever'
            WHEN oracle_text ILIKE E'%doctor''s companion%' THEN 'doctors_companion'
            ELSE 'other'
          END as ptype, COUNT(*) as cnt
        FROM cards
        WHERE oracle_text ILIKE '%partner%'
           OR oracle_text ILIKE '%choose a background%'
           OR oracle_text ILIKE '%friends forever%'
           OR oracle_text ILIKE E'%doctor''s companion%'
        GROUP BY 1 ORDER BY 2 DESC
    """)).all()
    for r in rows:
        print(f"{r.ptype}: {r.cnt}")

    bg = s.execute(
        text(
            "SELECT COUNT(*) FROM cards WHERE type_line ILIKE '%background%' AND is_legendary = true"
        )
    ).scalar()
    print(f"Background enchantments (legendary): {bg}")

    t = s.execute(
        text("SELECT oracle_text FROM cards WHERE name = 'Tymna the Weaver'")
    ).scalar()
    print(f"Tymna text: {repr(t) if t else None}")

    t2 = s.execute(
        text("SELECT oracle_text FROM cards WHERE name = 'Thrasios, Triton Hero'")
    ).scalar()
    print(f"Thrasios text: {repr(t2[:80]) if t2 else None}")

    # Named partner example
    t3 = s.execute(
        text("SELECT oracle_text FROM cards WHERE name = 'Regna, the Redeemer'")
    ).scalar()
    print(f"Regna text: {repr(t3[:120]) if t3 else None}")
