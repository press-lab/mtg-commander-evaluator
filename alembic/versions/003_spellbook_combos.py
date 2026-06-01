"""Add Commander Spellbook combo tables.

Revision ID: 003
Revises: 002
Create Date: 2026-05-31
"""

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spellbook_combos",
        sa.Column("spellbook_id", sa.Text, primary_key=True),
        sa.Column("card_count", sa.Integer, nullable=False),
        sa.Column("bracket_tag", sa.String(4), nullable=True),
        sa.Column(
            "is_commander_legal", sa.Boolean, nullable=False, server_default="true"
        ),
        sa.Column("results_description", sa.Text, nullable=True),
        sa.Column("fetched_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "spellbook_combo_cards",
        sa.Column(
            "id",
            sa.Text,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()::text"),
        ),
        sa.Column(
            "combo_id",
            sa.Text,
            sa.ForeignKey("spellbook_combos.spellbook_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("oracle_id", sa.Text, nullable=True),  # null if not in our card DB
        sa.Column("card_name", sa.Text, nullable=False),
    )

    op.create_index(
        "ix_spellbook_combo_cards_combo_id", "spellbook_combo_cards", ["combo_id"]
    )
    op.create_index(
        "ix_spellbook_combo_cards_oracle_id", "spellbook_combo_cards", ["oracle_id"]
    )
    op.create_index(
        "ix_spellbook_combos_bracket_tag", "spellbook_combos", ["bracket_tag"]
    )


def downgrade() -> None:
    op.drop_table("spellbook_combo_cards")
    op.drop_table("spellbook_combos")
