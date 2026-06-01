"""Add edhrec_card_stats table

Revision ID: 002
Revises: 001
Create Date: 2024-01-02 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "edhrec_card_stats",
        sa.Column("oracle_id", UUID(as_uuid=False), sa.ForeignKey("cards.oracle_id"), primary_key=True),
        sa.Column("card_name", sa.Text, nullable=False),
        sa.Column("num_decks", sa.Integer, nullable=False),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("fetched_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_edhrec_card_stats_rank", "edhrec_card_stats", ["rank"])


def downgrade() -> None:
    op.drop_table("edhrec_card_stats")
