"""Add card prices, EDHREC salt scores, and per-commander inclusion stats.

Revision ID: 005
Revises: 004
Create Date: 2026-06-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Budget filtering: representative USD price from Scryfall bulk data
    op.add_column(
        "cards",
        sa.Column("price_usd", sa.Numeric(10, 2), nullable=True),
    )

    # Salt tolerance: EDHREC community salt score (0-4 scale)
    op.add_column(
        "edhrec_card_stats",
        sa.Column("salt_score", sa.Numeric(4, 2), nullable=True),
    )

    # Per-commander EDHREC inclusion data (lazily fetched per commander page)
    op.create_table(
        "edhrec_commander_stats",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "commander_oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            nullable=False,
        ),
        sa.Column("card_name", sa.Text, nullable=False),
        sa.Column(
            "card_oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            nullable=True,
        ),
        sa.Column("num_decks", sa.Integer, nullable=False),
        sa.Column("potential_decks", sa.Integer, nullable=False),
        sa.Column("inclusion_rate", sa.Numeric(5, 4), nullable=False),
        # EDHREC synergy = inclusion here minus inclusion in other decks of
        # the same color identity; can be negative.
        sa.Column("synergy", sa.Numeric(6, 4), nullable=True),
        sa.Column("category", sa.Text, nullable=True),
        sa.Column("fetched_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint(
            "commander_oracle_id",
            "card_name",
            name="uq_edhrec_commander_stats_commander_card",
        ),
    )
    op.create_index(
        "ix_edhrec_commander_stats_commander",
        "edhrec_commander_stats",
        ["commander_oracle_id"],
    )
    op.create_index(
        "ix_edhrec_commander_stats_card_oracle",
        "edhrec_commander_stats",
        ["card_oracle_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_edhrec_commander_stats_card_oracle", table_name="edhrec_commander_stats"
    )
    op.drop_index(
        "ix_edhrec_commander_stats_commander", table_name="edhrec_commander_stats"
    )
    op.drop_table("edhrec_commander_stats")
    op.drop_column("edhrec_card_stats", "salt_score")
    op.drop_column("cards", "price_usd")
