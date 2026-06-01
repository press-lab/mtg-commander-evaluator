from mtg_evaluator.db.connection import get_session
from mtg_evaluator.db.models import CardClassification, CardFunction, CardBracketScore, Card
from sqlalchemy import select

CARDS = [
    "Cyclonic Rift", "Rhystic Study", "Demonic Tutor", "Swords to Plowshares",
    "Lightning Greaves", "Smothering Tithe", "Cultivate", "Counterspell",
    "Fierce Guardianship", "Vampiric Tutor", "Toxic Deluge", "Reanimate",
    "Mana Crypt", "Jeska's Will", "Deflecting Swat", "Dockside Extortionist",
    "Thassa's Oracle", "Mystic Remora", "Swan Song", "Thought Vessel",
]

with get_session() as s:
    for name in CARDS:
        card = s.scalars(select(Card).where(Card.name == name)).first()
        if not card:
            print(f"{name}: NOT IN DB")
            continue
        cls = s.scalars(
            select(CardClassification)
            .where(CardClassification.oracle_id == card.oracle_id)
            .where(CardClassification.is_valid == True)
        ).first()
        if not cls:
            print(f"{name}: NOT CLASSIFIED")
            continue

        fns = [f.function_name for f in s.scalars(
            select(CardFunction).where(CardFunction.classification_id == cls.id)
        ).all()]
        brackets = {b.bracket_level: b.score for b in s.scalars(
            select(CardBracketScore).where(CardBracketScore.classification_id == cls.id)
        ).all()}
        gp = cls.raw_output.get("general_commander_power")
        top_arch = sorted([(k,v) for k,v in cls.raw_output.get("archetype_fit", {}).items() if v], key=lambda x: x[1], reverse=True)[:3]
        warnings = cls.raw_output.get("warnings", [])
        context = cls.raw_output.get("commander_context_notes", [])

        b = brackets
        print(f"{name}")
        print(f"  functions : {fns}")
        print(f"  power     : {gp}/5  |  casual={b.get('casual')} b2={b.get('bracket_2')} b3={b.get('bracket_3')} b4={b.get('bracket_4')} cedh={b.get('cedh')}")
        print(f"  top arch  : {top_arch}")
        if warnings:
            print(f"  warnings  : {warnings}")
        if context:
            print(f"  context   : {context}")
        print()
