"""
Partner commander detection and lookup.

WotC has shipped five distinct "two-commander" mechanics. Each has different
rules for what can pair with what:

  generic_partner   — keyword "Partner"; any two cards that BOTH have it
                      (Tymna the Weaver + Thrasios, Triton Hero, etc.)

  named_partner     — "Partner with [Exact Name]"; ONLY that specific card
                      (Regna + Krav, Silvar + Trynn, etc.)

  choose_background — keyword "Choose a Background" on the creature commander;
                      pairs with any legendary enchantment with the Background
                      subtype (Commander Legends: Battle for Baldur's Gate)

  background        — the Background enchantment side of the above

  doctors_companion — keyword "Doctor's companion"; pairs with any legendary
                      creature whose name starts with "The " and is a Doctor
                      (Doctor Who Universes Beyond)

  friends_forever   — keyword "Friends forever"; any two cards that BOTH have it
                      (also Doctor Who Universes Beyond)

Detection is done entirely from oracle_text / type_line patterns — no extra DB
column needed. Scryfall's oracle text is consistent enough to regex reliably.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from mtg_evaluator.db.models import Card

PartnerType = Literal[
    "generic_partner",
    "named_partner",
    "choose_background",
    "background",
    "doctors_companion",
    "friends_forever",
]

# Regex patterns against oracle_text
_PARTNER_WITH_RE = re.compile(r"Partner with ([^(\n]+?)(?:\s*\(|\n|$)", re.IGNORECASE)
_GENERIC_PARTNER_RE = re.compile(
    r"\nPartner \(You can have two commanders", re.IGNORECASE
)
_CHOOSE_BG_RE = re.compile(r"Choose a Background", re.IGNORECASE)
_DOCTORS_RE = re.compile(r"Doctor'?s companion", re.IGNORECASE)
_FRIENDS_RE = re.compile(r"Friends forever", re.IGNORECASE)


@dataclass
class PartnerInfo:
    partner_type: PartnerType
    named_partner: str | None = None  # only set for named_partner
    label: str = ""  # human-readable description
    search_hint: str = ""  # UI hint for what to search


def detect_partner_type(card: Card) -> PartnerInfo | None:
    """
    Return PartnerInfo if this card has any partner mechanic, else None.
    Checks oracle_text and type_line; no DB queries needed.
    """
    text = card.oracle_text or ""
    type_line = card.type_line or ""

    # Named partner first (more specific than generic)
    m = _PARTNER_WITH_RE.search(text)
    if m:
        partner_name = m.group(1).strip()
        return PartnerInfo(
            partner_type="named_partner",
            named_partner=partner_name,
            label=f"Partner with {partner_name}",
            search_hint=f"Locked to: {partner_name}",
        )

    # Generic Partner keyword
    if _GENERIC_PARTNER_RE.search(text):
        return PartnerInfo(
            partner_type="generic_partner",
            label="Partner",
            search_hint="Any other commander with Partner",
        )

    # Choose a Background (creature side)
    if _CHOOSE_BG_RE.search(text):
        return PartnerInfo(
            partner_type="choose_background",
            label="Choose a Background",
            search_hint="Any legendary Background enchantment",
        )

    # Background enchantment (the other side)
    if "background" in type_line.lower() and card.is_legendary and not card.is_creature:
        return PartnerInfo(
            partner_type="background",
            label="Background",
            search_hint="Any legendary creature with 'Choose a Background'",
        )

    # Doctor's companion
    if _DOCTORS_RE.search(text):
        return PartnerInfo(
            partner_type="doctors_companion",
            label="Doctor's companion",
            search_hint="Any 'The Doctor' legendary creature",
        )

    # Friends forever
    if _FRIENDS_RE.search(text):
        return PartnerInfo(
            partner_type="friends_forever",
            label="Friends forever",
            search_hint="Any other commander with Friends forever",
        )

    return None


def find_valid_partners(session: Session, commander: Card) -> list[Card]:
    """
    Return all cards that can legally pair with this commander.
    Results are sorted by name for consistent display.
    """
    info = detect_partner_type(commander)
    if info is None:
        return []

    pt = info.partner_type

    if pt == "named_partner":
        # Only one valid partner — fetch it by name
        if not info.named_partner:
            return []
        card = session.scalars(
            select(Card).where(Card.name == info.named_partner)
        ).first()
        return [card] if card else []

    if pt == "generic_partner":
        # All legendary creatures with generic Partner (excluding self)
        rows = session.scalars(
            select(Card)
            .where(Card.oracle_id != commander.oracle_id)
            .where(Card.is_legendary == True)  # noqa: E712
            .where(
                Card.oracle_text.op("~*")(
                    r"(?n)\nPartner \(You can have two commanders"
                )
            )
            .order_by(Card.name)
        ).all()
        return list(rows)

    if pt == "choose_background":
        # All legendary Background enchantments
        rows = session.scalars(
            select(Card)
            .where(Card.is_legendary == True)  # noqa: E712
            .where(Card.type_line.ilike("%background%"))
            .order_by(Card.name)
        ).all()
        return list(rows)

    if pt == "background":
        # All legendary creatures with "Choose a Background"
        rows = session.scalars(
            select(Card)
            .where(Card.is_legendary == True)  # noqa: E712
            .where(Card.is_creature == True)  # noqa: E712
            .where(Card.oracle_text.ilike("%Choose a Background%"))
            .order_by(Card.name)
        ).all()
        return list(rows)

    if pt == "doctors_companion":
        # All "The Doctor" legendary creatures
        rows = session.scalars(
            select(Card)
            .where(Card.is_legendary == True)  # noqa: E712
            .where(Card.is_creature == True)  # noqa: E712
            .where(Card.name.ilike("The % Doctor%"))
            .order_by(Card.name)
        ).all()
        return list(rows)

    if pt == "friends_forever":
        # All cards with Friends forever (excluding self)
        rows = session.scalars(
            select(Card)
            .where(Card.oracle_id != commander.oracle_id)
            .where(Card.is_legendary == True)  # noqa: E712
            .where(Card.oracle_text.ilike("%Friends forever%"))
            .order_by(Card.name)
        ).all()
        return list(rows)

    return []


def combined_color_identity(commander: Card, partner: Card) -> list[str]:
    """Union of two commanders' color identities, sorted WUBRG order."""
    _ORDER = {"W": 0, "U": 1, "B": 2, "R": 3, "G": 4, "C": 5}
    colors = set(commander.color_identity or []) | set(partner.color_identity or [])
    colors.discard("C")  # colorless only applies when no other colors
    if not colors:
        colors = {"C"}
    return sorted(colors, key=lambda c: _ORDER.get(c, 9))
