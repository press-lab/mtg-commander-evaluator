# MTG Commander Evaluator — Technical Requirements

> Reference document for engineers joining mid-project. Covers architecture, data model, scoring design, API contract, and pending work.  
> Last updated: 2026-06-04

---

## 1. Product Purpose

A web application that helps Magic: The Gathering Commander players build better decks. Core value props:

- **Deck evaluation**: paste a decklist, receive bracket estimate, role coverage, combo detection, and structural warnings
- **Pool building**: pick a commander + archetype + bracket → receive a ranked candidate card pool with tier labels
- **Full deck assembly**: LLM selects the final 99 (or 98 with partner) cards from the pool, outputting a Moxfield-importable list
- **Commander discovery**: search commanders by archetype and color identity

The system is **data-first and deterministic-first**. LLMs are used for classification and final assembly, not for analysis and scoring. All bracket rules, nonbo detection, consistency math, and package health are computed deterministically.

---

## 2. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | Type-annotated throughout |
| Package manager | `uv` at `C:\Users\sethp\.local\bin\uv.exe` | `uv sync`, `uv run` |
| Web framework | FastAPI | Async handlers; Pydantic request/response schemas |
| Frontend | Alpine.js 3.x + Tailwind CSS (CDN) | Single `index.html` SPA, no build step |
| Database | PostgreSQL 17 (Docker) | Port **5433** (avoids local PG conflicts) |
| ORM | SQLAlchemy 2.x | Mapped columns, `ARRAY`, `JSONB`, `UUID` — **no SQLite** |
| Migrations | Alembic | Linear: 001 → 002 → 003 → 004 |
| LLM (primary) | DeepSeek via Anthropic SDK | `https://api.deepseek.com/anthropic`; model `deepseek-chat` |
| LLM (fallback) | Anthropic Claude | `claude-haiku-4-5-20251001` |
| Consistency math | Pure Python `math.comb` | No scipy dependency |

### LLM Integration Rules

- **API keys must NEVER be hardcoded.** All secrets come from `.env` only, accessed via `settings.deepseek_api_key` / `settings.anthropic_api_key`.
- DeepSeek is called via the Anthropic SDK with `thinking: {"type": "disabled"}` — the SDK interface is identical to Anthropic's, just different base URL and key.
- All LLM calls must have try/except with a deterministic fallback. The system must function (with degraded output) when LLMs are unavailable.
- Classifier: `"deepseek"` or `"anthropic"` — set via `settings.classifier`.

---

## 3. Environment & Configuration

### `.env` file (never commit)

```env
DATABASE_URL=postgresql://mtg_user:mtg_password@localhost:5433/mtg_evaluator
DEEPSEEK_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
CLASSIFIER=deepseek
```

### Docker (PostgreSQL)

```bash
docker compose up -d   # postgres:16 image, port 5433
```

### Running the app

```bash
uv run uvicorn mtg_evaluator.api.app:app --reload
# http://localhost:8000
```

### CLI commands

```bash
uv run mtg-evaluator ingest-scryfall          # download + store Scryfall oracle cards
uv run mtg-evaluator normalize-cards          # raw JSON → structured card tables (incl. price_usd)
uv run mtg-evaluator detect-card-changes      # queue classification jobs for new/changed cards
uv run mtg-evaluator classify-cards           # run LLM classifier on pending jobs
uv run mtg-evaluator ingest-edhrec-top        # pull top-5000 EDHREC popularity stats
uv run mtg-evaluator ingest-edhrec-salt       # pull EDHREC salt scores (curated fallback offline)
uv run mtg-evaluator ingest-spellbook         # pull all Commander Spellbook combos
uv run mtg-evaluator generate-profiles        # batch-generate commander profiles (--top N / --commander)
uv run mtg-evaluator serve                    # run the FastAPI app
```

---

## 4. Data Sources

### 4.1 Scryfall (oracle cards bulk download)

- URL: `https://api.scryfall.com/bulk-data` → `oracle_cards` type
- Ingested on-demand; raw JSON stored in `raw_scryfall_cards`
- Change detection via oracle_text checksum (SHA-256); changed cards re-queued for classification
- ~37,474 oracle cards total

### 4.2 EDHREC

- Top cards: `https://json.edhrec.com/pages/top/month.json` → top-5000 by global `num_decks` in `edhrec_card_stats`
- Salt scores: `https://json.edhrec.com/pages/top/salt.json` → `edhrec_card_stats.salt_score` (0–4 scale; curated ~50-card fallback list if offline)
- **Per-commander inclusion**: `https://json.edhrec.com/pages/commanders/<slug>.json` → `edhrec_commander_stats`. Lazily fetched on first pool build per commander (same pattern as commander profiles), cached 30 days (`edhrec_commander_cache_days`). Provides `inclusion_rate` and `synergy` (inclusion minus color-identity baseline; can be negative) per card.
- Fetch note: use plain `httpx.get` with a minimal `User-Agent` — heavy browser-mimicking headers trigger TLS-level resets from the EDHREC CDN.

### 4.3 Commander Spellbook

- REST API: `https://backend.commanderspellbook.com/api/v2/`
- 88,510 combos; 310,215 combo-card entries stored in `spellbook_combos` and `spellbook_combo_cards`
- Each combo has a bracket tag: `R`(ule 0), `S`(picy), `P`(owerful), `O`(verwhelmingly powerful), `C`(asual), `E`(xhibition)
- Ingestion uses periodic `session.commit()` every 5 pages to survive interruption

### 4.4 WotC Game Changers List

- Hardcoded in `evaluator.py` as `GAME_CHANGERS: frozenset[str]` — 53 cards (February 2026 list)
- B1/B2: 0 allowed. B3: ≤3. B4/B5: unlimited.
- Source: https://magic.wizards.com/en/news/announcements/commander-brackets-beta-update-february-9-2026

---

## 5. Database Schema

**Current Alembic head: `005_prices_salt_commander_stats`**

### Core card tables

| Table | Purpose |
|---|---|
| `card_ingestion_runs` | Tracks each Scryfall bulk download run |
| `raw_scryfall_cards` | Full raw JSON blob per card per ingestion run |
| `cards` | Normalized card data (CMC, type, colors, oracle text, checksum, etc.) |
| `card_faces` | DFC / split faces |
| `card_legalities` | Format legality per card |
| `card_keywords` | Keyword list |

### Classification tables

| Table | Purpose |
|---|---|
| `card_classification_jobs` | Job queue: pending → running → completed/failed |
| `card_classifications` | LLM output per card (versioned, validity-flagged) |
| `card_functions` | Exploded function tags (e.g., `ramp`, `removal`, `draw`) |
| `card_archetype_scores` | Archetype fit score per card per archetype (1–5) |
| `card_power_scores` | Role power score per card per role (1–5) |
| `card_bracket_scores` | Bracket fit score per card per bracket level (1–5) |
| `card_synergy_hooks` | Synergy pairing signals (future use) |

### Popularity / EDHREC

| Table | Purpose |
|---|---|
| `edhrec_card_stats` | Global `num_decks` count + `salt_score`, pulled monthly |
| `edhrec_commander_stats` | Per-commander card inclusion rate + synergy, lazily fetched per commander page |

### Combo tables

| Table | Purpose |
|---|---|
| `spellbook_combos` | Combo ID, bracket tag, result description |
| `spellbook_combo_cards` | Card membership in each combo (oracle_id + card_name) |

### Commander intelligence

| Table | Purpose |
|---|---|
| `commander_profiles` | LLM-derived per-commander analysis, cached with prompt version gating |

**`commander_profiles` key columns:**
- `oracle_id` (PK, FK → cards)
- `provides_draw`, `provides_ramp`, `provides_removal`, `provides_protection`, `provides_wincon`, `provides_tokens`, `provides_sac_outlet`, `provides_recursion`, `provides_graveyard_access`, `provides_exile_access`, `provides_cost_reduction`, `provides_combo_piece` — Boolean
- `needs_creatures`, `needs_artifacts`, `needs_spells`, `needs_lands`, `needs_combat`, `needs_attack_damage_triggers` — Boolean
- `dependency_score`, `protection_need`, `recast_importance` — Numeric(3,1), range 0–5
- `preferred_archetypes` — ARRAY(Text)
- `confidence` — Numeric(3,2)
- `prompt_version` — Text (gate: regenerate when `CURRENT_PROMPT_VERSION` bumps)
- `needs_review`, `manual_override` — Boolean (manual_override prevents LLM overwrite)

### Deck tracking (evaluation only)

| Table | Purpose |
|---|---|
| `decklists` | User-submitted decklists |
| `deck_cards` | Cards in each decklist |
| `deck_evaluations` | Evaluation results (bracket, archetype, scores, suggestions) |

---

## 6. Classification Schema

LLM output is validated against a strict Pydantic schema — no free-text fields.

```python
functions: list[str]          # e.g. ["ramp", "land_ramp"]
archetype_fit: dict[str, int] # archetype → score 1-5
bracket_fit: dict[str, int]   # bracket_key → score 1-5
role_power: dict[str, int]    # role → score 1-5
```

**Key function tags (subset):**
`ramp`, `land_ramp`, `mana_doubler`, `draw`, `cantrip`, `looting`, `removal`, `creature_removal`, `exile_removal`, `board_wipe`, `counter`, `protection`, `tutor`, `token_maker`, `sac_outlet`, `death_trigger`, `reanimation`, `self_mill`, `discard_outlet`, `etb_payoff`, `blink`, `artifact`, `enchantment`, `equipment`, `aura`, `combo_piece`, `finisher`, `landfall`, `extra_land_drop`

**Bracket keys:** `casual`, `bracket_2`, `bracket_3`, `bracket_4`, `cedh`

---

## 7. Bracket System (WotC Commander Brackets)

| Bracket | Label | Game Changers | Notes |
|---|---|---|---|
| B1 | Exhibition | 0 | Casual theme decks |
| B2 | Core | 0 | Precon-level |
| B3 | Upgraded | ≤3 | Focused synergy |
| B4 | Optimized | 4+ | Fast/consistent |
| B5 | cEDH | 4+ | Competitive |

**Hard floors (B4 minimum, always):**
- Mass land denial: Armageddon, Obliterate, etc. (22 cards hardcoded in `evaluator.py`)
- Any combo tagged `R` (Rule 0) or `S` (Spicy) in Spellbook

**Bracket combo allowance by tier** (defined in `request.py`):
- B1/B2: `{E, C, O}` — casual combos only
- B3: adds `{P, S}`
- B4/B5: adds `{R}` — all combos legal

---

## 8. Partner Mechanics

Five supported partner types (detected via oracle text in `partners.py`):

| Type | Description |
|---|---|
| `generic` | "Partner" — any two "Partner" commanders |
| `named_partner` | "Partner with [Name]" — only the specific named partner |
| `choose_background` | Human commander + Background enchantment |
| `doctors_companion` | Doctor creature + Companion Doctor Who card |
| `friends_forever` | Commander Legends: Battle for Baldur's Gate pairs |

Partner pairs: deck size is **98**, not 99. `has_partner = " + " in pool.commander_name`.

---

## 9. Card Pool Scoring (Phase 5)

### Composite formula

```
score = commander_fit×15 + archetype_fit×30 + role_quality×25
      + package_synergy×10 + bracket_fit×10 + combo_signal×7
      + popularity×3
      + affinity_bonus (per-commander EDHREC, when available)
```

All components normalized to 0–1 before weighting; final score capped at 100.

`affinity_bonus = inclusion_rate × 8 + max(0, synergy) × 12` — applied to non-lands only
(lands keep their tier-based scores). Inclusion rewards staples for THIS commander;
synergy rewards cards over-represented here vs. the color-identity baseline.

### Hard filters (before scoring)

- **Color identity** — card identity must be a subset of the deck's
- **Game Changers** — excluded below B3
- **Budget** — `max_card_price` (USD per card); unknown prices pass (almost always bulk)
- **Salt** — `salt_tolerance` any/low/medium/high → max salt 1.0/2.0/3.0; unsalted cards pass

### Component definitions

| Component | Source | Notes |
|---|---|---|
| `commander_fit` (0–1) | `_commander_fit_bonus()` in pool.py | Cards filling what the commander needs (creatures, artifacts, evasion, etc.) |
| `archetype_fit` (0–1) | `card_archetype_scores.score / 5` | LLM-assigned fit for the requested archetype |
| `role_quality` (0–1) | `best_role_quality() / 5` | 0–5 quality score from `role_quality.py` |
| `package_synergy` (0–1) | `_package_bonus()` | Enabler (0.7), payoff (0.7), support (0.3) of archetype package |
| `bracket_fit` (0–1) | `card_bracket_scores.score / 5` | LLM-assigned bracket appropriateness |
| `combo_signal` (0–1) | Spellbook match | Binary: 1.0 if card is in a legal combo |
| `popularity` (0–1) | `log10(num_decks) / 5` | Global EDHREC decks, log-scaled, capped at 1.0 |

### Land scoring (overrides composite)

Lands use tier-based scores from `lands.py` instead of the composite formula:
- T1 (fetch, original dual, shock): **92**
- T2 (check, pain, rainbow): **78**
- T3 (scry, filter): **65**
- T4 (slow, tapped dual): **50**
- T5 (enters tapped, no bonus): **30**
- Basics: **15**

Bracket-restricted lands (e.g. Ancient Tomb min B3, Mishra's Workshop min B4) return `None` for ineligible brackets.

### Tier assignment

| Tier | Criteria |
|---|---|
| `CORE` | In a legal combo, OR archetype_score ≥ 4 AND bracket_score ≥ 3 |
| `SUPPORT` | archetype_score ≥ 3, OR role_quality ≥ 2.5 |
| `FLEX` | Everything else passing color + bracket filter |

### Saturation / diminishing returns

`saturation_multiplier(role, current, target)` applied to SUPPORT + FLEX after CORE counts are locked:
- < 50% of target → ×1.3 (urgently needed)
- 80–100% of target → ×1.0 (on track)
- > 160% of target → ×0.4 (heavily over-represented)

---

## 10. Dynamic Role Targets

`role_targets.py` computes targets per role as:
```
target = base[bracket] + commander_modifier + archetype_modifier + curve_modifier
```
Clamped to `_MINIMUMS` (e.g. lands ≥ 28) and `_MAXIMUMS` (e.g. ramp ≤ 20).

### Base targets by bracket

| Bracket | Lands | Ramp | Draw | Removal | Wipes | Protection | Finisher |
|---|---|---|---|---|---|---|---|
| B1 | 38 | 10 | 8 | 5 | 2 | 2 | 3 |
| B2 | 37 | 11 | 9 | 6 | 2 | 2 | 3 |
| B3 | 36 | 12 | 10 | 7 | 2 | 3 | 4 |
| B4 | 32 | 14 | 10 | 6 | 2 | 3 | 4 |
| B5 | 29 | 16 | 11 | 5 | 1 | 4 | 3 |

### Commander profile modifiers (if profile available)

- `provides_draw` → draw −3
- `provides_ramp` → ramp −2
- `provides_protection` → protection −1
- `provides_wincon` → finisher −2
- `provides_removal` → removal −1
- `protection_need ≥ 4` → protection +2
- `dependency_score ≥ 4` → protection +1

### Curve modifier

- avg CMC > 4.5 → ramp +3
- avg CMC > 3.8 → ramp +1
- avg CMC < 2.5 → ramp −2, lands −1

---

## 11. Role Quality Scoring

`role_quality.py` returns 0–5 quality score per card/role. Baseline = 2.5.

**Adjustments:**
- CMC efficiency: 0-mana ramp = +1.8, 4-mana ramp = −0.5 (role-specific curves)
- Timing: instant removal/draw/counter = +0.5; sorcery removal = −0.2
- Removal scope: exile = +0.4, destroy = +0.1, bounce = −0.2
- Board wipe: exile all = +0.6, destroy all = +0.3
- Draw: repeatable = +0.6, draws 2+ at once = +0.3
- Double-duty: 3+ functions = +0.6, 2 functions = +0.3
- Game Changer status = +1.0

---

## 12. Package Health & Nonbo Detection

### Package health (`packages.py`)

14 archetypes with defined `PackageDefinition`:
- `enabler_functions`, `payoff_functions`, `support_functions`
- `min_enablers`, `min_payoffs`, `ideal_enabler_payoff_ratio`

`check_package_health()` returns `PackageHealth` with deficit counts and ratio warnings.

### Nonbo detection (`packages.py` → `detect_nonbos()`)

8 rule-based checks, each with `severity` (high/medium/low) and `confidence` (0–1):

| Rule ID | Trigger |
|---|---|
| `gy_deck_gy_hate` | Graveyard-hate cards in graveyard/reanimator deck |
| `go_wide_many_wipes` | ≥3 board wipes in tokens/aristocrats/combat deck |
| `high_cmc_low_ramp` | avg CMC > 4.0 with < 8 ramp pieces |
| `fast_deck_taplands` | > 6 taplands in combo/stax deck |
| `reanimator_no_enablers` | Reanimation payoffs but no discard/mill enablers |
| `no_enablers_for_payoffs` | Payoffs present but no enablers (tokens/aristocrats) |
| `no_finisher` | Combo/spellslinger has tutors but no win condition |
| `low_creature_count_combat` | < 20 creatures in combat/tribal/tokens deck |

---

## 13. Consistency Math

`consistency.py` — all hypergeometric, pure Python.

| Metric | Threshold for "good" | Formula |
|---|---|---|
| Lands in opener | ≥ 85% (2+ in 7 cards) | `prob_at_least(2, 99, land_count, 7)` |
| Ramp by turn 3 | ≥ 75% | `prob_at_least(1, 99, ramp_count, 9)` |
| Commander on curve | ≥ 65% | `prob_at_least(cmc, 99, land_count, 6+cmc)` |
| Interaction by turn 5 | ≥ 80% | `prob_at_least(1, 99, interaction_count, 11)` |
| Enabler + payoff by turn 6 | ≥ 70% | `P(enabler) × P(payoff)` (independence approx) |

Overall grade A–F from weighted average (lands 30%, ramp 25%, commander 20%, interaction 15%, enabler/payoff 10%).

---

## 14. Mana Analysis

`mana_analysis.py` — Karsten-derived thresholds for 99-card Commander.

- **Source requirements** by pip count × turn (e.g. 2 pips by turn 4 → 15 colored sources)
- **Known land color database**: 243+ lands with explicit color production sets
- Unknown lands assumed to produce all colors in the deck's identity (safe overestimate)
- **Pip stress score** (0–5): `n_colors_with_pips × 0.8 + max_pips × 0.6`
- **Cast reliability** (0–1): fraction of Karsten source requirements met per color

---

## 15. Archetype Normalization

`archetypes.py` — 25 canonical archetypes, 100+ player vocabulary synonyms.

**Canonical list:** `tokens`, `aristocrats`, `sacrifice`, `reanimator`, `graveyard`, `blink`, `voltron`, `spellslinger`, `control`, `stax`, `combo`, `midrange`, `aggro`, `tribal`, `big_mana`, `lands`, `artifacts`, `enchantress`, `toolbox`, `aura`, `combat`, `infect`, `superfriends`, `chaos`, `wheels`

**Key synonym mappings:** `"sac"→aristocrats`, `"wheels"→spellslinger`, `"hatebears"→stax`, `"landfall"→lands`, `"goblin"→tribal`, `"reanimation"→reanimator`, `"flicker"→blink`, `"storm"→spellslinger`

---

## 16. API Endpoints

All endpoints served by FastAPI at `http://localhost:8000`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/evaluate` | Evaluate a pasted decklist; returns bracket, scores, combos, gaps |
| `POST` | `/api/build` | Build a card pool (+ optional LLM assembly) |
| `GET` | `/api/commanders` | Find commanders by archetype and color |
| `GET` | `/api/browse` | Browse cards by archetype, bracket, role, color |
| `GET` | `/api/card/{oracle_id}` | Card detail: characteristics, functions, scores, EDHREC stats, combos |
| `GET` | `/api/archetypes` | Top archetypes for a commander |
| `GET` | `/api/archetypes/all` | Full canonical archetype list |
| `GET` | `/api/search/commanders` | Autocomplete commander search |
| `GET` | `/api/partners/{oracle_id}` | Partner mechanic info + valid partner list |
| `GET` | `/api/commander-info/{oracle_id}` | Combined: color identity, partner info, top archetypes |
| `GET` | `/` | Serve `index.html` SPA |

### `/api/build` response shape (Phase 5)

```json
{
  "commander": "Name [+ Partner]",
  "color_identity": ["B", "G"],
  "archetype": "aristocrats",
  "archetype_input": "sac",
  "bracket": 3,
  "total": 183,
  "core": [...],
  "support": [...],
  "flex": [...],
  "combos": [...],
  "commander_profile": { "provides_draw": false, "dependency_score": 3.5, ... },
  "role_targets": { "lands": 35, "ramp": 11, "draw": 9, ..., "modifiers_applied": [...] },
  "consistency": { "lands_in_opener": 0.91, "grade": "B", "overall_score": 0.78, "warnings": [...] },
  "package_health": { "is_healthy": false, "enabler_count": 3, "min_enablers": 4, "warnings": [...] },
  "nonbo_warnings": [{ "rule_id": "...", "severity": "high", "confidence": 0.9, "message": "..." }],
  "assembled": { "lands": [...], "ramp": [...], ..., "moxfield_text": "...", "validation_warnings": [...] }
}
```

---

## 17. LLM Assembly (Assembler)

`assembler.py` — calls the LLM once per build to select the final deck.

- Input: ranked `CardPool` with all tiers
- Output: `AssembledDeck` with categorized card lists + Moxfield export text
- Deck size: **98** if `has_partner`, else **99**
- `validate_assembled_deck()` checks: total card count, role minimums per bracket, land count
- Validation warnings surfaced in UI without blocking

**Bracket-aware role minimums for validation:**

| Bracket | Ramp min | Draw min | Removal min | Land min |
|---|---|---|---|---|
| B1 | 8 | 6 | 4 | 33 |
| B2 | 9 | 7 | 5 | 33 |
| B3 | 10 | 8 | 6 | 35 |
| B4 | 11 | 8 | 5 | 28 |
| B5 | 12 | 8 | 4 | 25 |

---

## 18. Source Module Map

```
src/mtg_evaluator/
├── cli.py                          # Typer CLI entrypoint
├── config.py                       # Settings (pydantic-settings, reads .env)
├── card_functions.py               # normalize_functions(), role_bucket() utilities
│
├── db/
│   ├── models.py                   # All SQLAlchemy models (17 tables + commander_profiles)
│   └── connection.py               # get_session() context manager
│
├── ingestion/
│   ├── scryfall.py                 # Scryfall bulk download + rate limiting
│   ├── storage.py                  # Raw JSON → DB
│   ├── edhrec.py                   # EDHREC top-N popularity scrape
│   └── spellbook.py                # Commander Spellbook combo ingestion (periodic commits)
│
├── normalization/
│   └── normalizer.py               # raw_scryfall_cards → cards + related tables
│
├── change_detection/
│   └── detector.py                 # Checksum diff → classification job queue
│
├── classification/
│   ├── schema.py                   # Pydantic CardClassification (strict enums, score ranges)
│   ├── deepseek.py                 # DeepSeek classifier (via Anthropic SDK)
│   ├── anthropic_classifier.py     # Claude classifier
│   ├── runner.py                   # Concurrent job processor (5 workers default)
│   └── stub_classifier.py          # Mock for testing
│
├── evaluation/
│   ├── evaluator.py                # evaluate_decklist() — bracket, combos, role coverage
│   ├── parser.py                   # Decklist text parser (Moxfield/MTGO/plain text)
│   ├── bracket_llm.py              # LLM bracket classification with deterministic fallback
│   └── game_changers.py            # GC list utilities
│
├── deckbuilding/
│   ├── pool.py                     # Phase 5: build_card_pool() — composite scoring, tiers
│   ├── assembler.py                # LLM final 99-card selection
│   ├── request.py                  # DeckRequest dataclass + bracket combo allowance
│   ├── commander_profile.py        # get_or_generate_profile() — LLM + DB cache
│   ├── role_quality.py             # 0–5 role quality score (CMC, timing, scope)
│   ├── role_targets.py             # Dynamic targets + saturation_multiplier()
│   ├── consistency.py              # Hypergeometric consistency math
│   ├── mana_analysis.py            # Karsten mana base analysis
│   ├── packages.py                 # Package health + nonbo detection (8 rules)
│   ├── archetypes.py               # Synonym normalization → 25 canonical archetypes
│   ├── lands.py                    # 243-land tier scoring (T1–T5 + bracket restrictions)
│   ├── partners.py                 # Partner mechanic detection + valid partner search
│   ├── find_commanders.py          # Commander search by archetype/color
│   ├── browse.py                   # Card browse with filters
│   └── intake.py                   # _top_archetypes() utility
│
└── api/
    ├── app.py                      # FastAPI routes
    └── static/
        └── index.html              # Alpine.js + Tailwind SPA
```

---

## 19. Pending Work

### Completed since first draft (June 2026)

- ✅ Mana analysis wired into evaluator, assembler, `/api/build`, and both UI tabs
- ✅ Deck evaluation Phase 5 enrichment (`deck_analysis.py`; structure score replaced old deck score)
- ✅ `generate-profiles` CLI command (batch + single, `--force`)
- ✅ `/api/card/{oracle_id}` detail endpoint
- ✅ Budget filter (`cards.price_usd` from Scryfall; `max_card_price` knob; estimated deck price)
- ✅ Salt tolerance (`edhrec_card_stats.salt_score`; any/low/medium/high knob)
- ✅ Per-commander EDHREC inclusion + synergy (`edhrec_commander_stats`, lazy fetch, affinity bonus in scoring)
- ✅ Commander profile + assembled-deck intelligence rendered in build tab

### High priority

1. **Card classification coverage**: ~4,708 of 37,474 oracle cards classified. Priority: top-5000 EDHREC cards. Current coverage handles the vast majority of played cards; expand to full set at the end.
2. **Integration tests**: broaden `pytest -m integration` coverage of the full pool → assemble → validate pipeline with budget/salt/synergy active.

### Future / Phase 6+

3. **Collaborative filtering / ML**: matrix factorization over per-commander inclusion data (now being accumulated in `edhrec_commander_stats`) plus card embeddings from oracle text. Deterministic logic remains primary.
4. **User accounts + saved decks**: allow users to save builds, track bracket history, and contribute inclusion data.
5. **Commander profile confidence threshold**: surface profiles with `confidence < 0.6` in the UI with a "needs review" badge; provide UI path for manual correction.
6. **Total-deck budget mode**: current budget is a per-card cap; a total-deck budget would need allocation logic in the assembler.
7. **Unpriced expensive cards**: cards whose Scryfall representative printing has no USD price (e.g. some reserved-list cards) pass the budget filter; consider a curated price floor list.

---

## 20. Key Design Decisions & Rationale

| Decision | Rationale |
|---|---|
| PostgreSQL-only (no SQLite) | ARRAY and JSONB columns are used extensively; SQLite doesn't support them |
| Deterministic scoring, LLM for classification only | LLM calls are slow and non-deterministic; bracket rules, package health, and math must be reproducible |
| `session.commit()` during ingestion loops | `session.flush()` inside a long loop loses all data if the process is killed. Periodic commits make ingestion restartable. |
| `best_role_quality()` replaces binary `fills_role` | Binary was coarse — a 1-mana Sol Ring and a 6-mana ramp spell filled the same "ramp" slot equally |
| Commander profile modifiers shape targets, don't collapse them | A draw-engine commander still needs some draw spells for redundancy and protection — the targets decrease but don't hit 0 |
| Archetype synonym normalization at intake | Players say "sac" and "wheels"; the system needs canonical names to query the DB |
| Partner deck size = 98 | Two commanders occupy the command zone; 98 + 2 = 100 card limit |
| Land quality tier overrides composite score | Composite score is archetype-aware; lands should be ranked by universal quality (fetch > shock > basic) regardless of archetype |
