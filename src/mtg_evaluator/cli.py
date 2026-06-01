import io
import json
import sys
from pathlib import Path
from typing import Optional

import typer

# Ensure stdout/stderr use UTF-8 on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

app = typer.Typer(
    name="mtg-evaluator", help="MTG Commander Evaluator — data pipeline CLI."
)


@app.command("ingest-scryfall")
def ingest_scryfall(
    use_cache: bool = typer.Option(
        False,
        "--use-cache",
        help="Use most recent cached bulk file instead of downloading.",
    ),
) -> None:
    """Download Scryfall oracle_cards bulk data and store raw JSON in the database."""
    from mtg_evaluator.db.connection import get_session
    from mtg_evaluator.ingestion.storage import run_ingestion

    with get_session() as session:
        run = run_ingestion(session, use_cache=use_cache)
        run_id, run_status = run.id, run.status
    typer.echo(f"Ingestion run {run_id} completed with status: {run_status}")


@app.command("normalize-cards")
def normalize_cards(
    run_id: Optional[str] = typer.Option(
        None,
        "--run-id",
        help="Specific ingestion run ID to normalize. Defaults to latest.",
    ),
) -> None:
    """Normalize raw Scryfall data into structured cards tables."""
    from mtg_evaluator.db.connection import get_session
    from mtg_evaluator.normalization.normalizer import normalize_all

    with get_session() as session:
        count = normalize_all(session, run_id=run_id)
    typer.echo(f"Normalized {count} cards.")


@app.command("detect-card-changes")
def detect_card_changes(
    run_id: Optional[str] = typer.Option(
        None,
        "--run-id",
        help="Specific ingestion run ID to diff against. Defaults to latest.",
    ),
) -> None:
    """Detect new or changed cards and create classification jobs."""
    from mtg_evaluator.db.connection import get_session
    from mtg_evaluator.change_detection.detector import detect_changes

    with get_session() as session:
        result = detect_changes(session, run_id=run_id)

    typer.echo(
        f"New: {result.new_cards}  Changed: {result.changed_cards}  "
        f"Unchanged: {result.unchanged_cards}  Jobs created: {result.jobs_created}"
    )


@app.command("ingest-edhrec-top")
def ingest_edhrec_top(
    json_file: Optional[Path] = typer.Option(
        None,
        "--json-file",
        help="Load from a locally saved EDHREC JSON instead of fetching.",
    ),
    save_json: Optional[Path] = typer.Option(
        None,
        "--save-json",
        help="Save raw EDHREC response to this path for inspection.",
    ),
) -> None:
    """Fetch EDHREC top cards by deck count and store in edhrec_card_stats."""
    from mtg_evaluator.db.connection import get_session
    from mtg_evaluator.ingestion.edhrec import run_edhrec_ingestion

    with get_session() as session:
        count = run_edhrec_ingestion(session, json_file=json_file, save_json=save_json)
    typer.echo(f"EDHREC ingestion complete: {count} cards stored.")


@app.command("ingest-spellbook")
def ingest_spellbook() -> None:
    """Fetch Commander Spellbook combo database and store in spellbook_combos tables."""
    from mtg_evaluator.db.connection import get_session
    from mtg_evaluator.ingestion.spellbook import run_spellbook_ingestion

    typer.echo("Fetching Commander Spellbook combos (paginated, may take ~1 minute)...")
    with get_session() as session:
        result = run_spellbook_ingestion(session)
    typer.echo(
        f"Done. Pages={result.pages_fetched}  Combos={result.combos_upserted}  "
        f"Combo cards={result.combo_cards_inserted}"
    )


@app.command("classify-cards")
def classify_cards(
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-n",
        help="Max number of jobs to process. Default: all pending.",
    ),
    reclassify: bool = typer.Option(
        False,
        "--reclassify",
        help="Re-classify cards that already have a valid classification.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Estimate token count and cost without making API calls.",
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        help="Override classifier provider: deepseek, anthropic, stub.",
    ),
    edhrec_only: bool = typer.Option(
        True,
        "--edhrec-only/--all-cards",
        help="Only classify cards present in edhrec_card_stats.",
    ),
) -> None:
    """Run LLM classification on pending card jobs."""
    from mtg_evaluator.classification.runner import run_classification
    from mtg_evaluator.config import settings

    if provider:
        settings.classifier = provider

    stats = run_classification(
        limit=limit, reclassify=reclassify, dry_run=dry_run, edhrec_only=edhrec_only
    )

    if not dry_run:
        typer.echo(
            f"Done. Total={stats.total} Succeeded={stats.succeeded} "
            f"Skipped={stats.skipped} Failed={stats.failed}"
        )


@app.command("validate-classification-schema")
def validate_classification_schema(
    file: Path = typer.Argument(
        ..., help="Path to a JSON file containing a classification object to validate."
    ),
) -> None:
    """Validate a JSON file against the CardClassification schema."""
    from mtg_evaluator.classification.validator import validate_classification

    if not file.exists():
        typer.echo(f"File not found: {file}", err=True)
        raise typer.Exit(1)

    with open(file, encoding="utf-8") as f:
        raw = json.load(f)

    is_valid, errors, classification = validate_classification(raw)

    if is_valid:
        typer.echo("✓ Valid classification schema.")
        if classification:
            typer.echo(
                f"  Card: {classification.card_name} ({classification.oracle_id})"
            )
            typer.echo(f"  Functions: {', '.join(classification.functions)}")
            typer.echo(f"  General power: {classification.general_commander_power}/5")
    else:
        typer.echo("✗ Validation failed:", err=True)
        for error in errors:
            typer.echo(f"  - {error}", err=True)
        raise typer.Exit(1)


@app.command("evaluate-deck")
def evaluate_deck(
    decklist_file: Optional[Path] = typer.Option(
        None, "--file", "-f", help="Path to decklist text file."
    ),
    name: str = typer.Option("My Deck", "--name", "-n", help="Deck name."),
) -> None:
    """Evaluate a Commander decklist — role coverage, bracket estimate, archetype, suggestions."""
    from mtg_evaluator.db.connection import get_session
    from mtg_evaluator.evaluation.parser import parse_decklist
    from mtg_evaluator.evaluation.evaluator import evaluate_decklist

    if decklist_file:
        raw = decklist_file.read_text(encoding="utf-8")
    else:
        typer.echo(
            "Paste your decklist below (blank line + Ctrl-Z on Windows to finish):"
        )
        lines = []
        try:
            while True:
                lines.append(input())
        except EOFError:
            pass
        raw = "\n".join(lines)

    with get_session() as session:
        parsed = parse_decklist(raw, session)
        result = evaluate_decklist(session, parsed, deck_name=name)

    typer.echo(f"\n=== {name} ===")
    if result.commander_name:
        typer.echo(f"Commander : {result.commander_name}")
    typer.echo(f"Archetype : {result.archetype_guess or 'unknown'}")
    typer.echo(f"Bracket   : {result.bracket_estimate or 'unknown'}")
    typer.echo(
        f"Synergy   : {result.synergy_score}/5"
        if result.synergy_score
        else "Synergy   : n/a"
    )

    typer.echo("\n--- Role Coverage ---")
    for rc in result.role_coverage:
        status = "OK" if rc.meets_minimum else f"LOW (need {rc.gap} more)"
        quality = (
            f"  quality={rc.average_quality:.1f}/5"
            if rc.average_quality is not None
            else ""
        )
        typer.echo(f"  {rc.function:<20} {rc.count:>3}  {status}{quality}")

    if result.consistency:
        typer.echo(
            f"\nConsistency: {result.consistency.grade} "
            f"({result.consistency.overall_score:.0%})"
        )
    if result.mana_analysis:
        typer.echo(
            f"Commander cast reliability: {result.mana_analysis.cast_reliability:.0%}"
        )
        for warning in result.mana_analysis.warnings:
            typer.echo(f"  ! {warning}")
    if result.package_health and result.package_health.warnings:
        typer.echo("\n--- Package Health ---")
        for warning in result.package_health.warnings:
            typer.echo(f"  ! {warning}")
    if result.nonbo_warnings:
        typer.echo("\n--- Nonbos ---")
        for warning in result.nonbo_warnings:
            typer.echo(f"  ! {warning.message}")

    if result.gaps:
        typer.echo("\n--- Gaps ---")
        for gap in result.gaps:
            typer.echo(f"  ! {gap}")

    if result.improvement_suggestions:
        typer.echo("\n--- Suggested Additions ---")
        for s in result.improvement_suggestions:
            typer.echo(f"  + {s['name']} (archetype score: {s['archetype_score']}/5)")

    if result.unresolved_cards:
        typer.echo(
            f"\n  {len(result.unresolved_cards)} card(s) not found in database: {', '.join(result.unresolved_cards[:5])}"
        )
    if result.unclassified_cards:
        typer.echo(
            f"  {len(result.unclassified_cards)} card(s) have no classification yet."
        )

    typer.echo(f"\nDecklist saved: {result.decklist_id}")


@app.command("find-commanders")
def find_commanders_cmd(
    archetype: str = typer.Argument(
        ..., help="Archetype to build around (e.g. sacrifice, tokens, midrange)."
    ),
    colors: Optional[str] = typer.Option(
        None,
        "--colors",
        "-c",
        help="Color filter, e.g. 'WUBR'. Commander must fit within these colors.",
    ),
    max_colors: Optional[int] = typer.Option(
        None,
        "--max-colors",
        help="Max number of colors (e.g. 2 = mono or two-color only).",
    ),
    limit: int = typer.Option(
        20, "--limit", "-n", help="Number of commanders to show."
    ),
) -> None:
    """Find commanders that support a given archetype. Starting point for archetype-first deck building."""
    from mtg_evaluator.db.connection import get_session
    from mtg_evaluator.deckbuilding.find_commanders import find_commanders

    color_list = list(colors.upper()) if colors else None

    with get_session() as session:
        options = find_commanders(
            session=session,
            archetype=archetype,
            colors=color_list,
            max_colors=max_colors,
            limit=limit,
        )

    if not options:
        typer.echo(
            f"No commanders found for archetype '{archetype}' with given filters."
        )
        raise typer.Exit(1)

    typer.echo(f"\n── Commanders for '{archetype}' ──\n")
    typer.echo(f"  {'Name':<35} {'Colors':<10} {'Arch':>5}  {'EDHREC':>7}")
    typer.echo(f"  {'-' * 35} {'-' * 10} {'-' * 5}  {'-' * 7}")
    for i, opt in enumerate(options, 1):
        colors_str = "".join(opt.color_identity) or "C"
        decks_str = f"{opt.edhrec_decks:,}" if opt.edhrec_decks else "—"
        typer.echo(
            f"  {i:>2}. {opt.name:<33} {colors_str:<10} {opt.archetype_score:>5.1f}  {decks_str:>7}"
        )

    typer.echo(
        f'\n  Run: mtg-evaluator build-deck "<name>" to build around one of these.'
    )


@app.command("browse-cards")
def browse_cards_cmd(
    archetype: Optional[str] = typer.Option(
        None, "--archetype", "-a", help="Archetype filter (e.g. sacrifice, tokens)."
    ),
    bracket: int = typer.Option(
        3, "--bracket", "-b", min=1, max=5, help="Bracket 1-5."
    ),
    role: Optional[str] = typer.Option(
        None,
        "--role",
        "-r",
        help="Role filter: ramp, draw, removal, tutor, board_wipe, protection.",
    ),
    colors: Optional[str] = typer.Option(
        None, "--colors", "-c", help="Color filter, e.g. 'BRG'."
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="Number of cards to show."),
) -> None:
    """Browse cards by archetype, bracket, and role — no commander needed."""
    from mtg_evaluator.db.connection import get_session
    from mtg_evaluator.deckbuilding.browse import browse_cards

    color_list = list(colors.upper()) if colors else None

    with get_session() as session:
        cards = browse_cards(
            session=session,
            archetype=archetype,
            bracket=bracket,
            role=role,
            colors=color_list,
            limit=limit,
        )

    if not cards:
        typer.echo("No cards found for the given filters.")
        raise typer.Exit(1)

    filter_desc = " | ".join(
        filter(
            None,
            [
                archetype,
                f"B{bracket}",
                f"role={role}" if role else None,
                f"colors={colors}" if colors else None,
            ],
        )
    )
    typer.echo(f"\n── Cards: {filter_desc} ──\n")
    typer.echo(f"  {'Name':<35} {'Score':>6}  {'Arch':>5}  {'Brkt':>5}  Functions")
    typer.echo(f"  {'-' * 35} {'-' * 6}  {'-' * 5}  {'-' * 5}  {'-' * 20}")
    for card in cards:
        gc = "★" if card.is_game_changer else " "
        arch_str = f"{card.archetype_score:.1f}" if card.archetype_score else "  —"
        brkt_str = f"{card.bracket_score:.1f}" if card.bracket_score else "  —"
        fns = ", ".join(card.functions[:3]) if card.functions else "—"
        typer.echo(
            f"  {gc}{card.name:<34} {card.score:>6.1f}  {arch_str:>5}  {brkt_str:>5}  {fns}"
        )


@app.command("build-deck")
def build_deck(
    commander: str = typer.Argument(..., help="Commander card name."),
    bracket: Optional[int] = typer.Option(
        None, "--bracket", "-b", min=1, max=5, help="Bracket 1-5."
    ),
    archetype: Optional[str] = typer.Option(
        None, "--archetype", "-a", help="Archetype (e.g. midrange, tokens, sacrifice)."
    ),
    combos: Optional[bool] = typer.Option(
        None, "--combos/--no-combos", help="Include combos?"
    ),
    tutors: Optional[str] = typer.Option(
        None, "--tutors", help="Tutor density: none, light, heavy."
    ),
    pool_size: int = typer.Option(
        300, "--pool-size", "-n", help="Number of cards to return."
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Use defaults for unspecified options (for scripting).",
    ),
) -> None:
    """Build a Commander deck card pool — ranked candidates matching your preferences."""
    import io

    if sys.stdout.encoding != "utf-8":
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace"
        )

    from mtg_evaluator.db.connection import get_session
    from mtg_evaluator.deckbuilding.intake import resolve_intake
    from mtg_evaluator.deckbuilding.pool import build_card_pool

    with get_session() as session:
        request = resolve_intake(
            session=session,
            commander_name=commander,
            bracket=bracket,
            archetype=archetype,
            want_combos=combos,
            tutor_density=tutors,
            interactive=not non_interactive,
        )
        request.pool_size = pool_size
        pool = build_card_pool(session, request)

    typer.echo(f"\n{'=' * 70}")
    typer.echo(
        f"  DECK POOL: {pool.commander_name}  |  {pool.request.bracket_label}  |  {pool.archetype or 'unknown'}"
    )
    typer.echo(
        f"  Colors: {' '.join(pool.color_identity or ['C'])}  |  "
        f"Combos: {'yes' if request.want_combos else 'no'}  |  "
        f"Tutors: {request.tutor_density}"
    )
    typer.echo(f"{'=' * 70}")

    if pool.combos:
        typer.echo(f"\n── COMBOS ({len(pool.combos)} available in color identity) ──")
        for c in pool.combos[:20]:
            typer.echo(f"  [{c.bracket_tag}] {' + '.join(c.card_names)}")
            if c.results_description:
                typer.echo(f"        → {c.results_description}")

    if pool.core:
        typer.echo(f"\n── CORE ({len(pool.core)} cards) ──")
        for card in pool.core:
            gc_flag = " ★" if card.is_game_changer else ""
            roles = ",".join(card.functions[:3]) if card.functions else "—"
            typer.echo(f"  {card.name:<35} score={card.score:>5}  [{roles}]{gc_flag}")

    if pool.support:
        typer.echo(f"\n── SUPPORT ({len(pool.support)} cards) ──")
        for card in pool.support:
            gc_flag = " ★" if card.is_game_changer else ""
            roles = ",".join(card.functions[:2]) if card.functions else "—"
            typer.echo(f"  {card.name:<35} score={card.score:>5}  [{roles}]{gc_flag}")

    if pool.flex:
        typer.echo(f"\n── FLEX ({len(pool.flex)} cards) ──")
        for card in pool.flex[:50]:  # cap flex display at 50
            typer.echo(f"  {card.name:<35} score={card.score:>5}")

    typer.echo(f"\n  Total: {pool.total} cards  |  {len(pool.combos)} combos")
    if request.free_text_notes:
        typer.echo(f"  Notes: {request.free_text_notes}")


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    port: int = typer.Option(8000, "--port", help="Port to listen on"),
    reload: bool = typer.Option(
        False, "--reload", help="Auto-reload on code changes (dev mode)"
    ),
) -> None:
    """Start the web UI server (FastAPI + Uvicorn)."""
    try:
        import uvicorn
    except ImportError:
        typer.echo("uvicorn not installed. Run: uv add 'uvicorn[standard]'", err=True)
        raise typer.Exit(1)

    typer.echo(f"Starting MTG Commander Evaluator at http://{host}:{port}")
    uvicorn.run(
        "mtg_evaluator.api.app:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
