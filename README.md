# MTG Commander Evaluator

A data-first backend for Commander-focused Magic: The Gathering deck analysis and card recommendations.

## Long-term product

A web app where users can:
- Pick colors, commander, bracket, budget, archetype, and salt tolerance to get Commander card/deck recommendations
- Upload a Commander decklist and receive evaluation: legality, archetype, bracket estimate, structural weaknesses, synergy quality, and suggested improvements
- Get recommendations grounded in structured card data, rules, bracket context, and Commander-specific card function — not generic LLM prose

## Phase 1: Data foundation

This repo builds the data brain that makes the future app possible.

**What Phase 1 delivers:**
- Full Scryfall oracle card ingestion with raw JSON storage
- Normalized card tables with structured fields
- Change detection: identifies new or oracle-text-changed cards
- Classification job queue for future LLM processing
- Strict Pydantic schema for card classifications (no loose prose)
- Stub classifier: end-to-end pipeline runs without LLM keys
- All 17 database tables with migrations

**What is not built yet:** frontend, recommender, ML model, chat interface.

---

## Getting started

### 1. Start the database

```bash
docker compose up -d
```

This starts a Postgres 16 instance on port **5433** (to avoid conflict with any local Postgres).

### 2. Install dependencies

```bash
pip install uv
uv sync
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env if your setup differs
```

### 4. Run migrations

```bash
uv run alembic upgrade head
```

---

## CLI commands

All commands are available via `uv run mtg-evaluator <command>`.

### `ingest-scryfall`

Downloads the Scryfall `oracle_cards` bulk file and stores all raw card JSON in the database.

```bash
uv run mtg-evaluator ingest-scryfall

# Use a previously downloaded cached file instead of re-downloading:
uv run mtg-evaluator ingest-scryfall --use-cache
```

### `normalize-cards`

Reads the latest ingestion run and normalizes card data into structured tables (`cards`, `card_legalities`, `card_faces`, `card_keywords`).

```bash
uv run mtg-evaluator normalize-cards

# Normalize from a specific ingestion run:
uv run mtg-evaluator normalize-cards --run-id <uuid>
```

### `detect-card-changes`

Compares oracle text checksums between the latest ingestion and the normalized `cards` table. Creates classification jobs for new and changed cards.

```bash
uv run mtg-evaluator detect-card-changes
```

### `validate-classification-schema`

Validates a JSON file against the `CardClassification` Pydantic schema. Use this to test classifier output before wiring in a real LLM.

```bash
uv run mtg-evaluator validate-classification-schema path/to/classification.json
```

**Example classification JSON:**
```json
{
  "oracle_id": "a2daf943-dc88-4c8b-ac97-4476ea6abb9c",
  "card_name": "Swords to Plowshares",
  "functions": ["creature_removal", "removal"],
  "zones_used": ["battlefield", "exile"],
  "timing": ["instant_speed"],
  "resources": ["mana"],
  "general_commander_power": 5,
  "role_power": {"removal_power": 5},
  "archetype_fit": {"control": 4, "toolbox": 5},
  "bracket_fit": {"bracket_2": 4, "bracket_3": 5, "bracket_4": 5, "cedh": 5},
  "commander_context_notes": [],
  "warnings": []
}
```

---

## Project structure

```
src/mtg_evaluator/
├── cli.py                    # Typer CLI entrypoint
├── config.py                 # Settings (DATABASE_URL, etc.)
├── db/
│   ├── connection.py         # SQLAlchemy engine and session context manager
│   └── models.py             # All 17 SQLAlchemy models
├── ingestion/
│   ├── scryfall.py           # Scryfall bulk download, rate limiting, caching
│   └── storage.py            # Raw JSON → raw_scryfall_cards table
├── normalization/
│   └── normalizer.py         # raw_scryfall_cards → cards + related tables
├── change_detection/
│   └── detector.py           # Diff checksums, create classification jobs
└── classification/
    ├── schema.py             # Pydantic CardClassification schema (strict enums)
    ├── validator.py          # Validate raw dict against schema
    ├── base.py               # Abstract BaseClassifier interface
    └── stub_classifier.py    # Mock classifier for pipeline testing
```

## Running tests

```bash
uv run pytest
```

Tests run without a database — they test ingestion logic, normalization field parsing, change detection state transitions, and classification schema validation using in-memory fixtures.

---

## Adding a real classifier

1. Implement `BaseClassifier` from `mtg_evaluator.classification.base`
2. Your `classify()` method receives `oracle_id`, `card_name`, `oracle_text`, `type_line`
3. Return a `CardClassification` — or return raw JSON and run it through `validate_classification()`
4. Store the result in `card_classifications` and explode into `card_functions`, `card_archetype_scores`, etc.

The schema enforces all enum values and score ranges (1–5). No free-text fields in classifications.
