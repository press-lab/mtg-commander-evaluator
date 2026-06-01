"""Initial schema

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "card_ingestion_runs",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("bulk_type", sa.String(64), nullable=False),
        sa.Column("card_count", sa.Integer, nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "running", "completed", "failed", name="ingestion_status"
            ),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text, nullable=True),
    )

    op.create_table(
        "raw_scryfall_cards",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("oracle_id", UUID(as_uuid=False), nullable=False),
        sa.Column("scryfall_id", UUID(as_uuid=False), nullable=False),
        sa.Column(
            "ingestion_run_id",
            UUID(as_uuid=False),
            sa.ForeignKey("card_ingestion_runs.id"),
            nullable=False,
        ),
        sa.Column("raw_json", JSONB, nullable=False),
        sa.Column("ingested_at", sa.DateTime, nullable=False),
        sa.Column("oracle_text_checksum", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_raw_scryfall_cards_oracle_id", "raw_scryfall_cards", ["oracle_id"]
    )
    op.create_index(
        "ix_raw_scryfall_cards_run_oracle",
        "raw_scryfall_cards",
        ["ingestion_run_id", "oracle_id"],
    )

    op.create_table(
        "cards",
        sa.Column("oracle_id", UUID(as_uuid=False), primary_key=True),
        sa.Column("scryfall_id", UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("mana_cost", sa.Text, nullable=True),
        sa.Column("cmc", sa.Numeric(6, 2), nullable=False),
        sa.Column("type_line", sa.Text, nullable=False),
        sa.Column("oracle_text", sa.Text, nullable=True),
        sa.Column("colors", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column(
            "color_identity", ARRAY(sa.String), nullable=False, server_default="{}"
        ),
        sa.Column("power", sa.String(8), nullable=True),
        sa.Column("toughness", sa.String(8), nullable=True),
        sa.Column("loyalty", sa.String(8), nullable=True),
        sa.Column("is_legendary", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_creature", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "is_planeswalker", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column("is_land", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_instant", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_sorcery", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("layout", sa.String(32), nullable=False),
        sa.Column("rarity", sa.String(16), nullable=False),
        sa.Column("set_code", sa.String(8), nullable=False),
        sa.Column("scryfall_uri", sa.Text, nullable=False),
        sa.Column("image_uri", sa.Text, nullable=True),
        sa.Column("oracle_text_checksum", sa.String(64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_cards_name", "cards", ["name"])

    op.create_table(
        "card_legalities",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            nullable=False,
        ),
        sa.Column("format", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.UniqueConstraint(
            "oracle_id", "format", name="uq_card_legalities_oracle_format"
        ),
    )

    op.create_table(
        "card_faces",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            nullable=False,
        ),
        sa.Column("face_index", sa.Integer, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("mana_cost", sa.Text, nullable=True),
        sa.Column("type_line", sa.Text, nullable=True),
        sa.Column("oracle_text", sa.Text, nullable=True),
        sa.Column("power", sa.String(8), nullable=True),
        sa.Column("toughness", sa.String(8), nullable=True),
        sa.Column("loyalty", sa.String(8), nullable=True),
        sa.Column("colors", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.UniqueConstraint(
            "oracle_id", "face_index", name="uq_card_faces_oracle_face"
        ),
    )

    op.create_table(
        "card_keywords",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            nullable=False,
        ),
        sa.Column("keyword", sa.Text, nullable=False),
        sa.UniqueConstraint(
            "oracle_id", "keyword", name="uq_card_keywords_oracle_keyword"
        ),
    )

    op.create_table(
        "card_classification_jobs",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("pending", "running", "completed", "failed", name="job_status"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("oracle_text_checksum", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_classification_jobs_oracle_id", "card_classification_jobs", ["oracle_id"]
    )
    op.create_index(
        "ix_classification_jobs_status", "card_classification_jobs", ["status"]
    )

    op.create_table(
        "card_classifications",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=False),
            sa.ForeignKey("card_classification_jobs.id"),
            nullable=False,
        ),
        sa.Column("classified_at", sa.DateTime, nullable=False),
        sa.Column("classifier_version", sa.String(64), nullable=False),
        sa.Column("raw_output", JSONB, nullable=False),
        sa.Column("is_valid", sa.Boolean, nullable=False),
        sa.Column("validation_errors", JSONB, nullable=True),
    )
    op.create_index(
        "ix_card_classifications_oracle_id", "card_classifications", ["oracle_id"]
    )

    op.create_table(
        "card_functions",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            nullable=False,
        ),
        sa.Column(
            "classification_id",
            UUID(as_uuid=False),
            sa.ForeignKey("card_classifications.id"),
            nullable=False,
        ),
        sa.Column("function_name", sa.String(64), nullable=False),
    )
    op.create_index("ix_card_functions_oracle_id", "card_functions", ["oracle_id"])

    op.create_table(
        "card_archetype_scores",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            nullable=False,
        ),
        sa.Column(
            "classification_id",
            UUID(as_uuid=False),
            sa.ForeignKey("card_classifications.id"),
            nullable=False,
        ),
        sa.Column("archetype", sa.String(64), nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
    )
    op.create_index(
        "ix_card_archetype_scores_oracle_id", "card_archetype_scores", ["oracle_id"]
    )

    op.create_table(
        "card_power_scores",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            nullable=False,
        ),
        sa.Column(
            "classification_id",
            UUID(as_uuid=False),
            sa.ForeignKey("card_classifications.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
    )
    op.create_index(
        "ix_card_power_scores_oracle_id", "card_power_scores", ["oracle_id"]
    )

    op.create_table(
        "card_bracket_scores",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            nullable=False,
        ),
        sa.Column(
            "classification_id",
            UUID(as_uuid=False),
            sa.ForeignKey("card_classifications.id"),
            nullable=False,
        ),
        sa.Column("bracket_level", sa.String(16), nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
    )
    op.create_index(
        "ix_card_bracket_scores_oracle_id", "card_bracket_scores", ["oracle_id"]
    )

    op.create_table(
        "card_synergy_hooks",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            nullable=False,
        ),
        sa.Column(
            "classification_id",
            UUID(as_uuid=False),
            sa.ForeignKey("card_classifications.id"),
            nullable=False,
        ),
        sa.Column("hook", sa.String(128), nullable=False),
    )
    op.create_index(
        "ix_card_synergy_hooks_oracle_id", "card_synergy_hooks", ["oracle_id"]
    )

    op.create_table(
        "archetypes",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column(
            "typical_colors", ARRAY(sa.String), nullable=False, server_default="{}"
        ),
        sa.Column(
            "key_mechanics", ARRAY(sa.String), nullable=False, server_default="{}"
        ),
    )

    op.create_table(
        "commander_rules_context",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("rule_key", sa.String(128), nullable=False, unique=True),
        sa.Column("rule_value", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("effective_date", sa.Date, nullable=False),
    )

    op.create_table(
        "bracket_context",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("bracket_level", sa.String(16), nullable=False, unique=True),
        sa.Column("display_name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("philosophy", sa.Text, nullable=False),
        sa.Column("key_signals", JSONB, nullable=False),
        sa.Column("effective_date", sa.Date, nullable=False),
    )

    op.create_table(
        "decklists",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column(
            "commander_oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            nullable=True,
        ),
        sa.Column(
            "partner_oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            nullable=True,
        ),
        sa.Column("colors", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("raw_list", sa.Text, nullable=True),
    )

    op.create_table(
        "deck_cards",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "decklist_id",
            UUID(as_uuid=False),
            sa.ForeignKey("decklists.id"),
            nullable=False,
        ),
        sa.Column(
            "oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_commander", sa.Boolean, nullable=False, server_default="false"),
    )

    op.create_table(
        "deck_evaluations",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "decklist_id",
            UUID(as_uuid=False),
            sa.ForeignKey("decklists.id"),
            nullable=False,
        ),
        sa.Column("evaluated_at", sa.DateTime, nullable=False),
        sa.Column("bracket_estimate", sa.String(16), nullable=True),
        sa.Column("archetype_guess", sa.String(64), nullable=True),
        sa.Column("legality_issues", JSONB, nullable=False, server_default="{}"),
        sa.Column("structural_notes", JSONB, nullable=False, server_default="{}"),
        sa.Column("synergy_score", sa.Numeric(4, 2), nullable=True),
        sa.Column(
            "improvement_suggestions", JSONB, nullable=False, server_default="{}"
        ),
    )

    op.execute("""
        INSERT INTO bracket_context (bracket_level, display_name, description, philosophy, key_signals, effective_date)
        VALUES
        ('casual', 'Casual (Bracket 1)', 'Kitchen table, precon-level decks.', 'Fun first, power second. No tutors, no fast mana, no combos.', '{"max_tutor_count": 0, "fast_mana": false, "combo_win": false, "expected_turn_win": null}', '2024-03-01'),
        ('bracket_2', 'Core (Bracket 2)', 'Upgraded precons, focused themes.', 'Synergistic but fair. Limited tutors, no one-card combos.', '{"max_tutor_count": 2, "fast_mana": false, "combo_win": false, "expected_turn_win": null}', '2024-03-01'),
        ('bracket_3', 'Optimized (Bracket 3)', 'Focused, powerful, non-CEDH decks.', 'High synergy, real win conditions. Fast mana ok. Multi-card combos ok.', '{"max_tutor_count": null, "fast_mana": true, "combo_win": true, "expected_turn_win": 8}', '2024-03-01'),
        ('bracket_4', 'High Power (Bracket 4)', 'Near-CEDH. High interaction, consistent combos.', 'Wins reliably by turns 5-7. Free interaction, fast mana, tutors.', '{"max_tutor_count": null, "fast_mana": true, "combo_win": true, "expected_turn_win": 6}', '2024-03-01'),
        ('cedh', 'CEDH (Bracket 5)', 'Competitive EDH. Best-in-slot everything.', 'Win as fast and consistently as possible. All tools allowed.', '{"max_tutor_count": null, "fast_mana": true, "combo_win": true, "expected_turn_win": 4}', '2024-03-01')
    """)

    op.execute("""
        INSERT INTO commander_rules_context (rule_key, rule_value, description, effective_date)
        VALUES
        ('singleton', 'true', 'Only one copy of each card except basic lands is allowed.', '2024-03-01'),
        ('deck_size', '100', 'Commander decks must contain exactly 100 cards including the commander.', '2024-03-01'),
        ('color_identity', 'strict', 'Cards in the deck may not have mana symbols outside the commander color identity.', '2024-03-01'),
        ('commander_zone', 'command_zone', 'The commander starts in and returns to the command zone on death or exile.', '2024-03-01'),
        ('commander_tax', '2', 'Each time a commander is cast from the command zone, it costs 2 more colorless mana.', '2024-03-01'),
        ('partner_rule', 'true', 'Cards with Partner may be used together as two commanders if both have partner.', '2024-03-01')
    """)


def downgrade() -> None:
    for table in [
        "deck_evaluations",
        "deck_cards",
        "decklists",
        "bracket_context",
        "commander_rules_context",
        "archetypes",
        "card_synergy_hooks",
        "card_bracket_scores",
        "card_power_scores",
        "card_archetype_scores",
        "card_functions",
        "card_classifications",
        "card_classification_jobs",
        "card_keywords",
        "card_faces",
        "card_legalities",
        "cards",
        "raw_scryfall_cards",
        "card_ingestion_runs",
    ]:
        op.drop_table(table)

    op.execute("DROP TYPE IF EXISTS ingestion_status")
    op.execute("DROP TYPE IF EXISTS job_status")
