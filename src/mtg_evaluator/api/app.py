"""
FastAPI application for MTG Commander Evaluator.

Endpoints:
  POST /api/evaluate          — paste a decklist, get bracket + score + suggestions
  POST /api/build             — build a card pool for a commander
  GET  /api/commanders        — find commanders for an archetype
  GET  /api/browse            — browse cards by archetype/bracket/role
  GET  /api/card/{oracle_id}  — card detail
  GET  /                      — serve the frontend SPA
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mtg_evaluator.db.connection import get_session
from mtg_evaluator.evaluation.parser import parse_decklist
from mtg_evaluator.evaluation.evaluator import evaluate_decklist
from mtg_evaluator.evaluation.deck_analysis import phase5_score_fields

app = FastAPI(title="MTG Commander Evaluator", version="0.1.0")

_STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class EvaluateRequest(BaseModel):
    decklist: str  # raw pasted deck text
    deck_name: str = "My Deck"


class BuildRequest(BaseModel):
    commander_name: str
    partner_name: str | None = None
    bracket: int = 3
    archetype: str | None = None
    want_combos: bool = True
    tutor_density: str = "light"
    pool_size: int = 200
    assemble: bool = False  # if True, run LLM assembler after building pool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BRACKET_INT = {
    "casual": 1,
    "bracket_1": 1,
    "bracket_2": 2,
    "bracket_3": 3,
    "bracket_4": 4,
    "cedh": 5,
}

_BRACKET_LABEL = {
    "casual": "B1 — Exhibition",
    "bracket_1": "B1 — Exhibition",
    "bracket_2": "B2 — Core",
    "bracket_3": "B3 — Upgraded",
    "bracket_4": "B4 — Optimized",
    "cedh": "B5 — cEDH",
}

_BRACKET_COLOR = {
    1: "#4ade80",
    2: "#86efac",
    3: "#facc15",
    4: "#f97316",
    5: "#ef4444",
}

_ROLE_ICON = {
    "ramp": "⚡",
    "draw": "🃏",
    "removal": "🗡️",
    "creature_removal": "🗡️",
    "board_wipe": "💥",
    "protection": "🛡️",
    "tutor": "🔍",
    "sacrifice": "💀",
    "token": "🪙",
    "counter": "🔢",
    "counterspell": "🔢",
    "lands": "🌍",
    "finisher": "🏁",
}


def _role_status(count: int, minimum: int) -> str:
    if count >= minimum:
        return "good"
    if count >= minimum * 0.6:
        return "warn"
    return "low"


def _serialize_commander_profile(profile):
    if not profile:
        return None
    return {
        "card_name": profile.card_name,
        "provides_draw": profile.provides_draw,
        "provides_ramp": profile.provides_ramp,
        "provides_removal": profile.provides_removal,
        "provides_protection": profile.provides_protection,
        "provides_wincon": profile.provides_wincon,
        "provides_tokens": profile.provides_tokens,
        "provides_sac_outlet": profile.provides_sac_outlet,
        "provides_recursion": profile.provides_recursion,
        "provides_combo_piece": profile.provides_combo_piece,
        "needs_creatures": profile.needs_creatures,
        "needs_artifacts": profile.needs_artifacts,
        "needs_spells": profile.needs_spells,
        "needs_combat": profile.needs_combat,
        "dependency_score": profile.dependency_score,
        "protection_need": profile.protection_need,
        "recast_importance": profile.recast_importance,
        "preferred_archetypes": profile.preferred_archetypes,
        "confidence": profile.confidence,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/api/evaluate")
async def evaluate(req: EvaluateRequest):
    """
    Parse a pasted decklist, evaluate it, and return:
      - bracket estimate + reasoning
      - deck score (0-100)
      - role coverage
      - game changers found
      - combos detected
      - upgrade / downgrade suggestions
    """
    try:
        with get_session() as session:
            parsed = parse_decklist(req.decklist, session)
            result = evaluate_decklist(session, parsed, deck_name=req.deck_name)
            session.commit()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {e}")

    bracket_int = _BRACKET_INT.get(result.bracket_estimate or "", 3)

    # Phase 5 structure score from the actual pasted decklist.
    total_cards = len(parsed.all_cards) or 1
    score_fields = phase5_score_fields(result, total_cards)

    # Role coverage serialization
    role_rows = [
        {
            "role": rc.function,
            "icon": _ROLE_ICON.get(rc.function, "📌"),
            "count": rc.count,
            "minimum": rc.minimum,
            "status": _role_status(rc.count, rc.minimum),
            "gap": rc.gap,
            "average_quality": rc.average_quality,
        }
        for rc in result.role_coverage
    ]
    role_actuals = {rc.function: rc.count for rc in result.role_coverage}
    role_quality = {
        rc.function: rc.average_quality
        for rc in result.role_coverage
        if rc.average_quality is not None
    }
    missing_roles = [
        {
            "role": rc.function,
            "actual": rc.count,
            "target": rc.minimum,
            "missing": rc.gap,
        }
        for rc in result.role_coverage
        if not rc.meets_minimum
    ]
    role_target_modifiers = (
        result.role_targets.modifiers_applied if result.role_targets else []
    )

    # Suggestions: upgrade = top cards NOT in deck; downgrade = remove GCs
    upgrade_suggestions = result.improvement_suggestions[:8]
    downgrade_suggestions = []
    if result.game_changers_found and bracket_int >= 3:
        # Suggest removing GCs to drop a bracket
        downgrade_suggestions = [
            {
                "name": gc,
                "reason": f"Game Changer — removing this (and others) could lower your bracket",
            }
            for gc in result.game_changers_found[:5]
        ]
    if result.mass_land_denial_found:
        downgrade_suggestions += [
            {
                "name": n,
                "reason": "Mass land denial — hard B4 floor, remove to drop to B3",
            }
            for n in result.mass_land_denial_found[:3]
        ]

    # Combo details
    combo_list = [
        {
            "id": c.spellbook_id,
            "tag": c.bracket_tag,
            "cards": c.card_names,
            "result": c.results_description,
        }
        for c in result.combos_found[:10]
    ]

    return {
        "commander": result.commander_name,
        "archetype": result.archetype_guess,
        "bracket_raw": result.bracket_estimate,
        "bracket_int": bracket_int,
        "bracket_label": _BRACKET_LABEL.get(
            result.bracket_estimate or "", result.bracket_estimate
        ),
        "bracket_color": _BRACKET_COLOR.get(bracket_int, "#888"),
        "bracket_reasoning": result.bracket_reasoning,
        **score_fields,
        "game_changers": result.game_changers_found,
        "combos": combo_list,
        "mass_land_denial": result.mass_land_denial_found,
        "role_coverage": role_rows,
        "role_actuals": role_actuals,
        "role_quality": role_quality,
        "missing_roles": missing_roles,
        "modifiers_applied": role_target_modifiers,
        "commander_profile": _serialize_commander_profile(result.commander_profile),
        "role_targets": (
            {
                **result.role_targets.to_dict(),
                "modifiers_applied": role_target_modifiers,
            }
            if result.role_targets
            else None
        ),
        "mana_analysis": (
            result.mana_analysis.to_dict() if result.mana_analysis else None
        ),
        "consistency": result.consistency.to_dict() if result.consistency else None,
        "package_health": (
            result.package_health.to_dict() if result.package_health else None
        ),
        "nonbo_warnings": [w.to_dict() for w in result.nonbo_warnings],
        "gaps": result.gaps,
        "upgrade_suggestions": upgrade_suggestions,
        "downgrade_suggestions": downgrade_suggestions,
        "unclassified_count": len(result.unclassified_cards),
        "unresolved_count": len(result.unresolved_cards),
        "unclassified_cards": result.unclassified_cards[:20],
    }


@app.post("/api/build")
async def build_pool(req: BuildRequest):
    """Build a ranked card pool for a commander."""
    from mtg_evaluator.deckbuilding.request import DeckRequest
    from mtg_evaluator.deckbuilding.pool import build_card_pool
    from mtg_evaluator.deckbuilding.intake import _top_archetypes
    from sqlalchemy import select
    from mtg_evaluator.db.models import Card

    try:
        with get_session() as session:
            # If no archetype given, pick the top one
            archetype = req.archetype
            if not archetype:
                commander = session.scalars(
                    select(Card).where(Card.name == req.commander_name)
                ).first()
                if commander:
                    tops = _top_archetypes(session, commander.oracle_id, top_n=1)
                    archetype = tops[0][0] if tops else None

            deck_req = DeckRequest(
                commander_name=req.commander_name,
                partner_name=req.partner_name,
                bracket=req.bracket,
                archetype=archetype,
                want_combos=req.want_combos,
                tutor_density=req.tutor_density,  # type: ignore[arg-type]
                pool_size=req.pool_size,
            )
            pool = build_card_pool(session, deck_req)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Optional LLM assembly — runs outside session (no DB needed)
    assembled = None
    if req.assemble:
        try:
            from mtg_evaluator.deckbuilding.assembler import (
                assemble_deck,
                validate_assembled_deck,
            )

            assembled_deck = assemble_deck(pool, bracket=req.bracket)
            warnings = validate_assembled_deck(assembled_deck, pool.role_targets)
            assembled = {
                "lands": assembled_deck.lands,
                "ramp": assembled_deck.ramp,
                "draw": assembled_deck.draw,
                "removal": assembled_deck.removal,
                "synergy": assembled_deck.synergy,
                "combo": assembled_deck.combo,
                "other": assembled_deck.other,
                "reasoning": assembled_deck.reasoning,
                "total": assembled_deck.total,
                "expected_total": 98 if assembled_deck.has_partner else 99,
                "moxfield_text": assembled_deck.to_moxfield(),
                "validation_warnings": warnings,
                "repair_notes": assembled_deck.repair_notes,
                "mana_analysis": (
                    assembled_deck.mana_analysis.to_dict()
                    if assembled_deck.mana_analysis
                    else None
                ),
                "consistency": (
                    assembled_deck.consistency.to_dict()
                    if assembled_deck.consistency
                    else None
                ),
                "package_health": (
                    assembled_deck.package_health.to_dict()
                    if assembled_deck.package_health
                    else None
                ),
                "nonbo_warnings": [
                    warning.to_dict() for warning in assembled_deck.nonbo_warnings
                ],
            }
        except Exception as e:
            assembled = {"error": str(e)}

    def _serialize_card(c):
        return {
            "name": c.name,
            "oracle_id": c.oracle_id,
            "type_line": c.type_line,
            "tier": c.tier,
            "score": c.score,
            "archetype_score": c.archetype_score,
            "bracket_score": c.bracket_score,
            "role_quality": c.role_quality,
            "role_quality_by_role": c.role_quality_by_role,
            "functions": c.functions,
            "is_game_changer": c.is_game_changer,
            "combo_ids": c.combo_ids,
            "edhrec_decks": c.edhrec_decks,
        }

    # --- Phase 5: serialize intelligence layers ---
    role_targets_out = pool.role_targets.to_dict() if pool.role_targets else None
    if pool.role_targets:
        role_targets_out["modifiers_applied"] = pool.role_targets.modifiers_applied

    consistency_out = pool.consistency.to_dict() if pool.consistency else None

    package_health_out = pool.package_health.to_dict() if pool.package_health else None

    nonbo_out = [w.to_dict() for w in pool.nonbo_warnings]

    profile_out = None
    if pool.commander_profile:
        p = pool.commander_profile
        profile_out = {
            "card_name": p.card_name,
            "provides_draw": p.provides_draw,
            "provides_ramp": p.provides_ramp,
            "provides_removal": p.provides_removal,
            "provides_protection": p.provides_protection,
            "provides_wincon": p.provides_wincon,
            "provides_tokens": p.provides_tokens,
            "provides_sac_outlet": p.provides_sac_outlet,
            "provides_recursion": p.provides_recursion,
            "provides_combo_piece": p.provides_combo_piece,
            "needs_creatures": p.needs_creatures,
            "needs_artifacts": p.needs_artifacts,
            "needs_spells": p.needs_spells,
            "needs_combat": p.needs_combat,
            "dependency_score": p.dependency_score,
            "protection_need": p.protection_need,
            "recast_importance": p.recast_importance,
            "preferred_archetypes": p.preferred_archetypes,
            "confidence": p.confidence,
        }

    return {
        "commander": pool.commander_name,
        "assembled": assembled,
        "color_identity": pool.color_identity,
        "archetype": pool.archetype,  # canonical (normalized) archetype
        "archetype_input": req.archetype,  # what the user typed
        "bracket": req.bracket,
        "total": pool.total,
        "core": [_serialize_card(c) for c in pool.core],
        "support": [_serialize_card(c) for c in pool.support],
        "flex": [_serialize_card(c) for c in pool.flex],
        "combos": [
            {
                "id": c.spellbook_id,
                "tag": c.bracket_tag,
                "cards": c.card_names,
                "result": c.results_description,
            }
            for c in pool.combos
        ],
        # Phase 5
        "commander_profile": profile_out,
        "role_targets": role_targets_out,
        "consistency": consistency_out,
        "package_health": package_health_out,
        "nonbo_warnings": nonbo_out,
    }


@app.get("/api/commanders")
async def commanders(
    archetype: str = Query(..., description="e.g. sacrifice, tokens, tribal"),
    colors: str | None = Query(None, description="Comma-separated colors e.g. B,G"),
    max_colors: int | None = Query(None),
    limit: int = Query(20, le=50),
):
    from sqlalchemy import select
    from mtg_evaluator.deckbuilding.find_commanders import find_commanders
    from mtg_evaluator.deckbuilding.partners import detect_partner_type
    from mtg_evaluator.db.models import Card

    color_list = [c.strip() for c in colors.split(",")] if colors else None
    with get_session() as session:
        results = find_commanders(
            session,
            archetype=archetype,
            colors=color_list,
            max_colors=max_colors,
            limit=limit,
        )
        # Enrich with partner info — serialize inside session
        oracle_ids = [c.oracle_id for c in results]
        cards_by_id = {
            card.oracle_id: card
            for card in session.scalars(
                select(Card).where(Card.oracle_id.in_(oracle_ids))
            ).all()
        }
        enriched = []
        for c in results:
            card = cards_by_id.get(c.oracle_id)
            partner_info = detect_partner_type(card) if card else None
            enriched.append(
                {
                    "name": c.name,
                    "oracle_id": c.oracle_id,
                    "color_identity": list(c.color_identity or []),
                    "archetype_score": c.archetype_score,
                    "edhrec_decks": c.edhrec_decks,
                    "type_line": c.type_line,
                    "partner_label": partner_info.label if partner_info else None,
                    "partner_type": partner_info.partner_type if partner_info else None,
                }
            )

    return enriched


@app.get("/api/browse")
async def browse(
    archetype: str | None = Query(None),
    bracket: int = Query(3, ge=1, le=5),
    role: str | None = Query(None, description="ramp, draw, removal, tutor, etc."),
    colors: str | None = Query(None, description="Comma-separated e.g. B,G"),
    limit: int = Query(50, le=100),
):
    from mtg_evaluator.deckbuilding.browse import browse_cards

    color_list = [c.strip() for c in colors.split(",")] if colors else None
    with get_session() as session:
        results = browse_cards(
            session,
            archetype=archetype,
            bracket=bracket,
            role=role,
            colors=color_list,
            limit=limit,
        )

    return [
        {
            "name": c.name,
            "oracle_id": c.oracle_id,
            "type_line": c.type_line,
            "archetype_score": c.archetype_score,
            "bracket_score": c.bracket_score,
            "functions": c.functions,
            "is_game_changer": c.is_game_changer,
            "edhrec_decks": c.edhrec_decks,
            "score": c.score,
        }
        for c in results
    ]


@app.get("/api/archetypes")
async def archetypes(commander_name: str = Query(...)):
    """Return available archetypes for a commander, for the build form's dropdown."""
    from mtg_evaluator.deckbuilding.intake import _top_archetypes
    from mtg_evaluator.deckbuilding.archetypes import CANONICAL_ARCHETYPES
    from sqlalchemy import select
    from mtg_evaluator.db.models import Card

    with get_session() as session:
        commander = session.scalars(
            select(Card).where(Card.name == commander_name)
        ).first()
        if not commander:
            raise HTTPException(status_code=404, detail="Commander not found")
        tops = _top_archetypes(session, commander.oracle_id, top_n=6)

    return {
        "commander_archetypes": [{"archetype": a, "score": s} for a, s in tops],
        "all_archetypes": CANONICAL_ARCHETYPES,
    }


@app.get("/api/archetypes/all")
async def all_archetypes():
    """Return the full list of canonical archetypes (for dropdowns with no commander selected)."""
    from mtg_evaluator.deckbuilding.archetypes import CANONICAL_ARCHETYPES

    return {"archetypes": CANONICAL_ARCHETYPES}


@app.get("/api/search/commanders")
async def search_commanders(q: str = Query(..., min_length=2), limit: int = 10):
    """Autocomplete search for commander names."""
    from sqlalchemy import select, func
    from mtg_evaluator.db.models import Card

    with get_session() as session:
        rows = session.execute(
            select(Card.name, Card.oracle_id, Card.color_identity, Card.type_line)
            .where(Card.is_legendary == True)  # noqa: E712
            .where(func.lower(Card.name).contains(q.lower()))
            .limit(limit)
        ).all()

    return [
        {
            "name": r.name,
            "oracle_id": r.oracle_id,
            "color_identity": r.color_identity,
            "type_line": r.type_line,
        }
        for r in rows
    ]


@app.get("/api/partners/{oracle_id}")
async def partners(oracle_id: str):
    """
    Given a commander's oracle_id, return:
      - partner_info: what partner mechanic the commander has (if any)
      - valid_partners: list of cards that can legally pair with it

    Returns partner_info: null if the commander has no partner mechanic.
    """
    from mtg_evaluator.db.models import Card
    from mtg_evaluator.deckbuilding.partners import (
        detect_partner_type,
        find_valid_partners,
    )

    with get_session() as session:
        commander = session.get(Card, oracle_id)
        if not commander:
            raise HTTPException(status_code=404, detail="Commander not found")

        info = detect_partner_type(commander)
        if info is None:
            return {"partner_info": None, "valid_partners": []}

        valid = find_valid_partners(session, commander)
        # Serialize inside session to avoid DetachedInstanceError
        partner_info_out = {
            "type": info.partner_type,
            "label": info.label,
            "named_partner": info.named_partner,
            "search_hint": info.search_hint,
        }
        valid_out = [
            {
                "name": c.name,
                "oracle_id": c.oracle_id,
                "color_identity": list(c.color_identity or []),
                "type_line": c.type_line,
            }
            for c in valid
        ]

    return {"partner_info": partner_info_out, "valid_partners": valid_out}


@app.get("/api/commander-info/{oracle_id}")
async def commander_info(oracle_id: str):
    """
    Full commander info: color identity, partner mechanic, top archetypes.
    Used by the build form after selecting a commander.
    """
    from mtg_evaluator.db.models import Card
    from mtg_evaluator.deckbuilding.partners import detect_partner_type
    from mtg_evaluator.deckbuilding.intake import _top_archetypes

    with get_session() as session:
        commander = session.get(Card, oracle_id)
        if not commander:
            raise HTTPException(status_code=404, detail="Commander not found")

        info = detect_partner_type(commander)
        archetypes = _top_archetypes(session, oracle_id, top_n=6)
        # Serialize inside session
        out = {
            "name": commander.name,
            "oracle_id": commander.oracle_id,
            "color_identity": list(commander.color_identity or []),
            "type_line": commander.type_line,
            "partner_info": (
                {
                    "type": info.partner_type,
                    "label": info.label,
                    "named_partner": info.named_partner,
                    "search_hint": info.search_hint,
                }
                if info
                else None
            ),
            "archetypes": [{"archetype": a, "score": s} for a, s in archetypes],
        }

    return out


# ---------------------------------------------------------------------------
# Serve frontend
# ---------------------------------------------------------------------------


@app.get("/")
async def index():
    return FileResponse(_STATIC_DIR / "index.html")


# Mount static files (CSS, JS, etc. if we add them)
if (_STATIC_DIR).exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
