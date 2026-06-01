"""
Decklist parser — accepts standard text format and resolves card names to oracle_ids.

Supported formats:
    1 Sol Ring
    1x Sol Ring
    Sol Ring          (quantity assumed 1)

Commander marked by appending *CMDR* or placing before a blank line headed "Commander".
MTGO/Moxfield export format also supported.
"""
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from mtg_evaluator.db.models import Card

_COMMANDER_ELIGIBLE_TEXT = "can be your commander"


def _is_valid_commander(card: Card) -> bool:
    """A card is a legal commander if it is:
    - A legendary creature
    - A legendary planeswalker with 'can be your commander' oracle text
    - A legendary Background enchantment (partner for 'Choose a Background' commanders)
    - Any legendary card with 'can be your commander' in its oracle text
    """
    if not card.is_legendary:
        return False
    if card.is_creature:
        return True
    text = (card.oracle_text or "").lower()
    type_line = (card.type_line or "").lower()
    if _COMMANDER_ELIGIBLE_TEXT in text:
        return True
    if card.is_planeswalker:
        return False  # planeswalkers need the explicit text (checked above)
    if "background" in type_line:
        return True  # Legendary Enchantment — Background cards are legal partner commanders
    return False


@dataclass
class ParsedCard:
    raw_name: str
    oracle_id: str
    quantity: int
    is_commander: bool


@dataclass
class ParsedDecklist:
    commander: ParsedCard | None
    partner: ParsedCard | None
    cards: list[ParsedCard]
    unresolved: list[str] = field(default_factory=list)

    @property
    def all_cards(self) -> list[ParsedCard]:
        out = []
        if self.commander:
            out.append(self.commander)
        if self.partner:
            out.append(self.partner)
        out.extend(self.cards)
        return out


_QTY_RE = re.compile(r"^(\d+)x?\s+(.+)$", re.IGNORECASE)
_CMDR_MARKER = re.compile(r"\s*\*CMDR\*\s*", re.IGNORECASE)
_SECTION_RE = re.compile(r"^(Commander|Deck|Sideboard|Maybeboard)\s*$", re.IGNORECASE)


def _resolve_names(session: Session, names: list[str]) -> dict[str, str]:
    rows = session.execute(
        select(Card.name, Card.oracle_id).where(Card.name.in_(names))
    ).all()
    name_map = {row.name: row.oracle_id for row in rows}

    unmatched = [n for n in names if n not in name_map]
    if unmatched:
        # Try front face of split/DFC cards
        front_names = [n.split(" // ")[0] for n in unmatched]
        rows2 = session.execute(
            select(Card.name, Card.oracle_id).where(Card.name.in_(front_names))
        ).all()
        front_map = {row.name: row.oracle_id for row in rows2}
        for original, front in zip(unmatched, front_names):
            if front in front_map:
                name_map[original] = front_map[front]

    return name_map


def parse_decklist(raw: str, session: Session) -> ParsedDecklist:
    lines = [l.strip() for l in raw.strip().splitlines()]

    entries: list[tuple[str, int, bool]] = []  # (name, qty, is_commander)
    in_commander_section = False

    for line in lines:
        if not line or line.startswith("#"):
            continue

        if _SECTION_RE.match(line):
            in_commander_section = line.lower().startswith("commander")
            continue

        is_cmdr = bool(_CMDR_MARKER.search(line))
        line = _CMDR_MARKER.sub("", line).strip()

        m = _QTY_RE.match(line)
        if m:
            qty, name = int(m.group(1)), m.group(2).strip()
        else:
            qty, name = 1, line

        if not name:
            continue

        entries.append((name, qty, is_cmdr or in_commander_section))

    all_names = list({name for name, _, _ in entries})
    name_map = _resolve_names(session, all_names)

    commander = None
    partner = None
    cards = []
    unresolved = []

    for name, qty, is_cmdr in entries:
        oracle_id = name_map.get(name)
        if not oracle_id:
            unresolved.append(name)
            continue

        pc = ParsedCard(raw_name=name, oracle_id=oracle_id, quantity=qty, is_commander=is_cmdr)

        if is_cmdr:
            card_row = session.get(Card, oracle_id)
            if card_row and not _is_valid_commander(card_row):
                raise ValueError(
                    f"'{name}' is not a legal commander — must be a legendary creature "
                    "or a legendary planeswalker with 'can be your commander' in its text."
                )
            if commander is None:
                commander = pc
            elif partner is None:
                partner = pc
        else:
            cards.append(pc)

    return ParsedDecklist(
        commander=commander,
        partner=partner,
        cards=cards,
        unresolved=unresolved,
    )
