"""
Compare an assembled deck against the EDHREC consensus for its commander.

"Solved" needs receipts. For optimized builds the deck should largely agree
with what thousands of real decklists play for this commander — and every
disagreement should have a defensible reason. This module produces that
report: overlap with the consensus core, consensus cards we excluded (with
the reason derived from our pipeline), and our off-meta picks.

Derived exclusion reasons, in check order:
  bracket_filtered — Game Changer below B3 / mass land denial below B4
  salt_filtered    — above the requested salt tolerance
  budget_filtered  — above the per-card price cap
  not_classified   — card has no LLM classification yet, so it never
                     entered the candidate pool
  outscored        — was in the pool but lost on composite score to the
                     cards we picked (the honest "we disagree" case)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mtg_evaluator.deckbuilding.pool import CardPool
from mtg_evaluator.deckbuilding.request import SALT_THRESHOLDS
from mtg_evaluator.evaluation.game_changers import is_game_changer

# Inclusion rate above which a card counts as "consensus core" for the commander
CONSENSUS_THRESHOLD = 0.40
# Inclusion rate below which one of our picks counts as "off-meta"
OFF_META_THRESHOLD = 0.10


@dataclass
class ConsensusMiss:
    name: str
    inclusion_rate: float
    synergy: float | None
    reason: str  # bracket_filtered | salt_filtered | budget_filtered | not_classified | outscored
    detail: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "inclusion_rate": self.inclusion_rate,
            "synergy": self.synergy,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass
class OffMetaPick:
    name: str
    inclusion_rate: float | None  # None = EDHREC page doesn't list it at all
    score: float | None
    rationale: str | None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "inclusion_rate": self.inclusion_rate,
            "score": self.score,
            "rationale": self.rationale,
        }


@dataclass
class EdhrecComparison:
    consensus_size: int          # cards above CONSENSUS_THRESHOLD on EDHREC
    matched: list[str] = field(default_factory=list)
    missed: list[ConsensusMiss] = field(default_factory=list)
    off_meta: list[OffMetaPick] = field(default_factory=list)

    @property
    def overlap_pct(self) -> float:
        if not self.consensus_size:
            return 0.0
        return round(len(self.matched) / self.consensus_size, 3)

    def to_dict(self) -> dict:
        return {
            "consensus_size": self.consensus_size,
            "consensus_threshold": CONSENSUS_THRESHOLD,
            "overlap_pct": self.overlap_pct,
            "matched": self.matched,
            "missed": [m.to_dict() for m in self.missed],
            "off_meta": [p.to_dict() for p in self.off_meta],
        }


def _miss_reason(
    name: str,
    pool: CardPool,
    classified_names: set[str] | None,
) -> tuple[str, str]:
    """Why didn't the deck play this consensus card?"""
    request = pool.request

    if request.bracket < 3 and is_game_changer(name):
        return "bracket_filtered", f"Game Changer — not legal at B{request.bracket}"

    salt_cap = SALT_THRESHOLDS.get(request.salt_tolerance)
    pool_card = next((c for c in pool.all_cards if c.name == name), None)

    if pool_card is not None:
        # It made the candidate pool but wasn't selected
        return "outscored", (
            f"In the candidate pool at score {pool_card.score:.0f} — "
            "outscored by the selected cards for its role"
        )

    # Not in the pool: figure out which filter (or data gap) removed it
    if classified_names is not None and name not in classified_names:
        return "not_classified", "No LLM classification yet — never entered the pool"
    if request.max_card_price is not None:
        return "budget_filtered", (
            f"Likely above the ${request.max_card_price:.0f} per-card budget cap"
        )
    if salt_cap is not None:
        return "salt_filtered", (
            f"Likely above the '{request.salt_tolerance}' salt threshold ({salt_cap})"
        )
    if classified_names is not None and name in classified_names:
        return "outscored", (
            "Classified, but didn't make the trimmed candidate pool on composite score"
        )
    return "not_classified", "Not in the candidate pool (unclassified or filtered)"


def compare_to_edhrec(
    deck_card_names: list[str],
    pool: CardPool,
    cmd_stats: dict[str, dict],
    classified_names: set[str] | None = None,
) -> EdhrecComparison | None:
    """
    Compare the final deck against this commander's EDHREC consensus.
    Returns None when no per-commander data is available.

    classified_names: optional set of all classified card names, used to
    distinguish "not classified" from "filtered" for missed consensus cards.
    """
    if not cmd_stats:
        return None

    deck_names = set(deck_card_names)
    deck_front = {n.split(" // ")[0] for n in deck_names}

    consensus = {
        name: stat
        for name, stat in cmd_stats.items()
        if (stat.get("inclusion_rate") or 0) >= CONSENSUS_THRESHOLD
    }

    matched: list[str] = []
    missed: list[ConsensusMiss] = []
    for name, stat in sorted(
        consensus.items(), key=lambda kv: -(kv[1].get("inclusion_rate") or 0)
    ):
        if name in deck_names or name.split(" // ")[0] in deck_front:
            matched.append(name)
        else:
            reason, detail = _miss_reason(name, pool, classified_names)
            missed.append(
                ConsensusMiss(
                    name=name,
                    inclusion_rate=stat.get("inclusion_rate") or 0.0,
                    synergy=stat.get("synergy"),
                    reason=reason,
                    detail=detail,
                )
            )

    # Our picks EDHREC doesn't endorse (low or no inclusion data).
    # Basics and lands are skipped — land bases legitimately diverge.
    pool_by_name = {c.name: c for c in pool.all_cards}
    off_meta: list[OffMetaPick] = []
    for name in deck_card_names:
        card = pool_by_name.get(name)
        if card is None or "land" in card.type_line.lower():
            continue
        stat = cmd_stats.get(name) or cmd_stats.get(name.split(" // ")[0])
        inclusion = stat.get("inclusion_rate") if stat else None
        if inclusion is not None and inclusion >= OFF_META_THRESHOLD:
            continue
        off_meta.append(
            OffMetaPick(
                name=name,
                inclusion_rate=inclusion,
                score=card.score,
                rationale=None,  # filled by caller from deck.card_rationale
            )
        )
    off_meta.sort(key=lambda p: -(p.score or 0))

    return EdhrecComparison(
        consensus_size=len(consensus),
        matched=matched,
        missed=missed,
        off_meta=off_meta[:15],
    )
