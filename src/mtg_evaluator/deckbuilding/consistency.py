"""
Deck consistency calculators using hypergeometric distribution.

All math is pure Python (math.comb) — no scipy dependency.
Hypergeometric models drawing cards from a fixed deck without replacement.

For Commander (99-card singleton):
  N = deck size (typically 99 or 98 with partner)
  K = copies of the desired card type in deck (e.g., land count)
  n = cards seen (opening hand + draws)
  k = minimum number wanted
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb, log
from typing import Optional


def _hyper_pmf(k: int, N: int, K: int, n: int) -> float:
    """P(X = k) for Hypergeometric(N, K, n). Returns 0 for invalid parameters."""
    if k < 0 or k > min(K, n) or (n - k) > (N - K):
        return 0.0
    try:
        return comb(K, k) * comb(N - K, n - k) / comb(N, n)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _hyper_cdf(k: int, N: int, K: int, n: int) -> float:
    """P(X <= k) for Hypergeometric(N, K, n)."""
    return sum(_hyper_pmf(i, N, K, n) for i in range(max(0, k + 1)))


def prob_at_least(k: int, N: int, K: int, n: int) -> float:
    """P(X >= k) — probability of seeing at least k successes in n draws."""
    return round(1.0 - _hyper_cdf(k - 1, N, K, n), 4)


# ---------------------------------------------------------------------------
# Named calculators
# ---------------------------------------------------------------------------


def prob_lands_in_opener(
    land_count: int,
    deck_size: int = 99,
    hand_size: int = 7,
    min_lands: int = 2,
) -> float:
    """P(at least min_lands in opening hand). Sweet spot: > 0.90."""
    return prob_at_least(min_lands, deck_size, land_count, hand_size)


def prob_ramp_by_turn(
    ramp_count: int,
    deck_size: int = 99,
    target_turn: int = 3,
    min_ramp: int = 1,
) -> float:
    """
    P(at least min_ramp ramp pieces seen by turn target_turn).
    Cards seen = 7 (opener) + (target_turn - 1) draws.
    """
    cards_seen = 7 + (target_turn - 1)
    return prob_at_least(min_ramp, deck_size, ramp_count, cards_seen)


def prob_commander_on_curve(
    commander_cmc: float,
    land_count: int,
    deck_size: int = 99,
) -> float:
    """
    Approximate probability of having enough lands to cast the commander on curve
    (i.e., having ≥ commander_cmc lands by turn commander_cmc).

    Cards seen by turn T: 7 (opener) + (T-1) draws.
    This is a floor estimate — ramp and mana rocks are not counted here.
    """
    turn = max(1, int(commander_cmc))
    cards_seen = 7 + (turn - 1)
    lands_needed = turn  # naive: need T lands to cast a T-CMC spell
    return prob_at_least(lands_needed, deck_size, land_count, cards_seen)


def prob_interaction_by_turn(
    interaction_count: int,
    deck_size: int = 99,
    target_turn: int = 5,
    min_interaction: int = 1,
) -> float:
    """P(seeing at least min_interaction removal/counter/wipe by turn target_turn)."""
    cards_seen = 7 + (target_turn - 1)
    return prob_at_least(min_interaction, deck_size, interaction_count, cards_seen)


def prob_enabler_and_payoff(
    enabler_count: int,
    payoff_count: int,
    deck_size: int = 99,
    target_turn: int = 6,
) -> float:
    """
    Approximate P(seeing at least 1 enabler AND 1 payoff by turn N).
    Uses independence approximation: P(A∩B) ≈ P(A) × P(B).
    (True hypergeometric joint requires more complex calculation.)
    """
    cards_seen = 7 + (target_turn - 1)
    p_enabler = prob_at_least(1, deck_size, enabler_count, cards_seen)
    p_payoff = prob_at_least(1, deck_size, payoff_count, cards_seen)
    return round(p_enabler * p_payoff, 4)


# ---------------------------------------------------------------------------
# Consistency report
# ---------------------------------------------------------------------------


@dataclass
class ConsistencyReport:
    """All consistency metrics for a deck configuration."""

    lands_in_opener: float  # P(2+ lands in 7-card hand)
    ramp_by_turn3: float  # P(1+ ramp piece by turn 3)
    commander_on_curve: float  # P(enough lands to cast commander on curve)
    interaction_by_turn5: float  # P(1+ removal/counter/wipe by turn 5)
    enabler_payoff_by_turn6: float  # P(1+ enabler AND 1+ payoff by turn 6)

    land_count: int
    ramp_count: int
    interaction_count: int
    commander_cmc: float
    deck_size: int

    @property
    def overall_score(self) -> float:
        """
        Weighted average of consistency metrics (0-1).
        Lands opener and ramp weighted more heavily — fundamental consistency.
        """
        weights = [0.30, 0.25, 0.20, 0.15, 0.10]
        values = [
            self.lands_in_opener,
            self.ramp_by_turn3,
            self.commander_on_curve,
            self.interaction_by_turn5,
            self.enabler_payoff_by_turn6,
        ]
        return round(sum(w * v for w, v in zip(weights, values)), 3)

    @property
    def grade(self) -> str:
        s = self.overall_score
        if s >= 0.85:
            return "A"
        if s >= 0.75:
            return "B"
        if s >= 0.65:
            return "C"
        if s >= 0.55:
            return "D"
        return "F"

    def warnings(self) -> list[str]:
        """Return actionable warnings for low-scoring metrics."""
        w = []
        if self.lands_in_opener < 0.80:
            w.append(
                f"Low land count ({self.land_count}): only {self.lands_in_opener:.0%} chance of 2+ lands in opener. Add 2-3 more lands."
            )
        if self.ramp_by_turn3 < 0.70:
            w.append(
                f"Low ramp ({self.ramp_count} pieces): only {self.ramp_by_turn3:.0%} chance of ramp by turn 3. Add 2-3 ramp pieces."
            )
        if self.commander_on_curve < 0.60:
            w.append(
                f"Commander cast reliability low ({self.commander_on_curve:.0%}): CMC {self.commander_cmc:.0f} is hard to cast on curve with {self.land_count} lands."
            )
        if self.interaction_by_turn5 < 0.75:
            w.append(
                f"Low interaction ({self.interaction_count} pieces): only {self.interaction_by_turn5:.0%} chance of answer by turn 5."
            )
        return w

    def to_dict(self) -> dict:
        return {
            "lands_in_opener": self.lands_in_opener,
            "ramp_by_turn3": self.ramp_by_turn3,
            "commander_on_curve": self.commander_on_curve,
            "interaction_by_turn5": self.interaction_by_turn5,
            "enabler_payoff_by_turn6": self.enabler_payoff_by_turn6,
            "overall_score": self.overall_score,
            "grade": self.grade,
            "warnings": self.warnings(),
        }


def compute_consistency(
    land_count: int,
    ramp_count: int,
    interaction_count: int,
    commander_cmc: float,
    enabler_count: int = 0,
    payoff_count: int = 0,
    deck_size: int = 99,
) -> ConsistencyReport:
    return ConsistencyReport(
        lands_in_opener=prob_lands_in_opener(land_count, deck_size),
        ramp_by_turn3=prob_ramp_by_turn(ramp_count, deck_size),
        commander_on_curve=prob_commander_on_curve(
            commander_cmc, land_count, deck_size
        ),
        interaction_by_turn5=prob_interaction_by_turn(interaction_count, deck_size),
        enabler_payoff_by_turn6=prob_enabler_and_payoff(
            enabler_count, payoff_count, deck_size
        ),
        land_count=land_count,
        ramp_count=ramp_count,
        interaction_count=interaction_count,
        commander_cmc=commander_cmc,
        deck_size=deck_size,
    )
