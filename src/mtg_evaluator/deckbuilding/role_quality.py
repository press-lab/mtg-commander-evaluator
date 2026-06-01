"""
Role quality scoring — replaces binary fills_role.

Returns a 0-5 quality score for a card in a given role, based on:
  - CMC efficiency
  - Timing (instant > sorcery for interaction)
  - Scope (exile > destroy > bounce for removal)
  - Repeatability (triggered/activated > one-shot)
  - Flexibility (double-duty bonus)
  - Game Changer status

No new LLM calls — uses data we already have: cmc, type_line, oracle_text, functions.
"""

from __future__ import annotations

import re
from typing import Mapping

from mtg_evaluator.card_functions import normalize_functions, role_bucket

# Removal quality signals in oracle text
_EXILE_PATTERNS = re.compile(r"\bexile\b", re.IGNORECASE)
_DESTROY_PATTERNS = re.compile(r"\bdestroy\b", re.IGNORECASE)
_BOUNCE_PATTERNS = re.compile(r"\breturn\b.*\bto (its|their) owner", re.IGNORECASE)
_EACH_CREATURE = re.compile(r"each (creature|permanent|nonland)", re.IGNORECASE)

# Repeatability signals
_REPEATABLE = re.compile(
    r"(whenever|at the beginning|at end of (turn|each|combat)|once each (turn|round)|"
    r"activated ability|{T}:|\btap\b.*:)",
    re.IGNORECASE,
)

# Draw quantity signals
_DRAW_N = re.compile(r"draw (two|three|four|five|six|\d+) card", re.IGNORECASE)

# Ramp signals
_TUTOR_LAND = re.compile(r"search (your|their) library.*land", re.IGNORECASE)
_ADD_MANA_ANY = re.compile(r"add.*mana of any (color|type)", re.IGNORECASE)


def _cmc_efficiency(cmc: float, role: str) -> float:
    """CMC modifier: lower is generally better, but role matters."""
    role = role_bucket(role)
    # Different roles have different CMC sweet spots
    if role in ("ramp",):
        # 0-1 mana ramp is exceptional; 4+ is usually bad
        if cmc == 0:
            return 1.8
        if cmc == 1:
            return 1.2
        if cmc == 2:
            return 0.6
        if cmc == 3:
            return 0.0
        if cmc == 4:
            return -0.5
        return -1.2
    elif role in ("removal", "counterspell", "protection"):
        if cmc == 0:
            return 1.5
        if cmc == 1:
            return 1.0
        if cmc == 2:
            return 0.5
        if cmc == 3:
            return 0.0
        if cmc == 4:
            return -0.4
        if cmc == 5:
            return -0.8
        return -1.3
    elif role in ("draw",):
        if cmc == 0:
            return 1.0
        if cmc == 1:
            return 0.8
        if cmc == 2:
            return 0.4
        if cmc == 3:
            return 0.0
        if cmc == 4:
            return -0.3
        if cmc == 5:
            return -0.7
        return -1.2
    elif role in ("board_wipe",):
        # Wipes are expected to cost 4-5
        if cmc <= 2:
            return 0.5  # cheap wipe = very good
        if cmc <= 4:
            return 0.2
        if cmc == 5:
            return 0.0
        if cmc == 6:
            return -0.2
        return -0.5
    else:
        # Generic: smooth penalty
        if cmc <= 1:
            return 0.8
        if cmc <= 2:
            return 0.4
        if cmc <= 3:
            return 0.0
        if cmc <= 5:
            return -0.3
        return -0.8


def compute_role_quality(
    role: str,
    cmc: float,
    is_instant: bool,
    is_sorcery: bool,
    functions: list[str],
    is_game_changer: bool,
    oracle_text: str = "",
) -> float:
    """
    Returns a 0-5 quality score for a card filling the given role.
    Higher is better. 2.5 is baseline neutral.
    """
    role = role_bucket(role)
    functions = normalize_functions(functions)
    text = oracle_text or ""
    base = 2.5

    # CMC efficiency
    base += _cmc_efficiency(cmc, role)

    # Timing bonus (instant > sorcery for interaction)
    if role in ("removal", "counterspell", "protection", "draw") and is_instant:
        base += 0.5
    elif role in ("removal", "counterspell", "protection") and is_sorcery:
        base -= 0.2  # sorcery interaction is slower

    # Removal scope: exile > destroy > bounce
    if role == "removal":
        if _EXILE_PATTERNS.search(text):
            base += 0.4
        elif _DESTROY_PATTERNS.search(text):
            base += 0.1
        elif _BOUNCE_PATTERNS.search(text):
            base -= 0.2  # bounce lets them replay
        # Symmetric/mass effects are lower quality as single-target removal
        if _EACH_CREATURE.search(text):
            base -= 0.3  # use board_wipe role for these

    # Board wipe: exile all > destroy all > conditional
    if role == "board_wipe":
        if _EXILE_PATTERNS.search(text) and _EACH_CREATURE.search(text):
            base += 0.6
        elif _DESTROY_PATTERNS.search(text) and _EACH_CREATURE.search(text):
            base += 0.3

    # Draw repeatability
    if role == "draw":
        if _REPEATABLE.search(text):
            base += 0.6  # ongoing draw engine
        if _DRAW_N.search(text):
            base += 0.3  # draws 2+ at once

    # Ramp quality signals
    if role == "ramp":
        if _TUTOR_LAND.search(text):
            base += 0.2  # land tutor = reliable
        if _ADD_MANA_ANY.search(text):
            base += 0.2  # flexible color fixing

    # Repeatability bonus for any role
    if _REPEATABLE.search(text) and role in ("ramp", "draw", "tutor"):
        base += 0.3

    # Double-duty bonus: card fills multiple roles
    n_roles = len(functions)
    if n_roles >= 3:
        base += 0.6
    elif n_roles >= 2:
        base += 0.3

    # Game Changer = independently verified high quality
    if is_game_changer:
        base += 1.0

    return round(min(max(base, 0.0), 5.0), 2)


def role_quality_map(
    functions: list[str],
    cmc: float,
    is_instant: bool,
    is_sorcery: bool,
    is_game_changer: bool,
    oracle_text: str = "",
) -> dict[str, float]:
    """
    Return per-role quality scores for every role this card fills.
    """
    if not functions:
        return {}
    roles = sorted({role_bucket(fn) for fn in normalize_functions(functions)})
    return {
        role: compute_role_quality(
            role,
            cmc,
            is_instant,
            is_sorcery,
            functions,
            is_game_changer,
            oracle_text,
        )
        for role in roles
    }


def need_weighted_role_quality(
    role_scores: Mapping[str, float],
    role_needs: Mapping[str, int] | None = None,
) -> float:
    """
    Collapse per-role quality into one composite input using current role gaps.

    Multi-role cards get the most credit when they cover roles the deck still
    needs, and saturated roles still count a little without dominating the card.
    """
    if not role_scores:
        return 0.0

    needs = role_needs or {}
    total_weight = 0.0
    weighted_quality = 0.0
    covered_needed_roles = 0

    for role, quality in role_scores.items():
        gap = max(0, needs.get(role, 1))
        if gap > 0:
            weight = 1.0 + min(gap, 4) * 0.2
            covered_needed_roles += 1
        else:
            weight = 0.35
        total_weight += weight
        weighted_quality += quality * weight

    if total_weight == 0:
        return 0.0

    multi_role_bonus = min(0.5, max(0, covered_needed_roles - 1) * 0.2)
    return round(min(5.0, weighted_quality / total_weight + multi_role_bonus), 2)


def best_role_quality(
    functions: list[str],
    cmc: float,
    is_instant: bool,
    is_sorcery: bool,
    is_game_changer: bool,
    oracle_text: str = "",
) -> float:
    """
    Backward-compatible max role quality score across all roles this card fills.
    Prefer role_quality_map + need_weighted_role_quality for new scoring.
    """
    return max(
        role_quality_map(
            functions,
            cmc,
            is_instant,
            is_sorcery,
            is_game_changer,
            oracle_text,
        ).values(),
        default=0.0,
    )
