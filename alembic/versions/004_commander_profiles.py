"""Add commander_profiles table for Phase 5 deck intelligence.

Revision ID: 004
Revises: 003
Create Date: 2026-06-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commander_profiles",
        sa.Column(
            "oracle_id",
            UUID(as_uuid=False),
            sa.ForeignKey("cards.oracle_id"),
            primary_key=True,
        ),
        sa.Column("card_name", sa.Text, nullable=False),
        # What the commander provides — reduces deck's need for these roles
        sa.Column("provides_draw", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("provides_ramp", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "provides_tokens", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column(
            "provides_sac_outlet", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column(
            "provides_recursion", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column(
            "provides_graveyard_access",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "provides_exile_access", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column(
            "provides_cost_reduction",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "provides_removal", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column(
            "provides_protection", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column(
            "provides_wincon", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column(
            "provides_combo_piece", sa.Boolean, nullable=False, server_default="false"
        ),
        # What the commander needs — increases deck's requirement for these
        sa.Column(
            "needs_creatures", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column(
            "needs_artifacts", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column("needs_spells", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("needs_lands", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("needs_combat", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "needs_attack_damage_triggers",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
        # 0-5 risk/dependency scores
        sa.Column(
            "dependency_score", sa.Numeric(3, 1), nullable=False, server_default="0"
        ),
        sa.Column(
            "protection_need", sa.Numeric(3, 1), nullable=False, server_default="0"
        ),
        sa.Column(
            "recast_importance", sa.Numeric(3, 1), nullable=False, server_default="0"
        ),
        # Archetypes this commander is suited for
        sa.Column(
            "preferred_archetypes", ARRAY(sa.Text), nullable=False, server_default="{}"
        ),
        # Metadata / cache invalidation
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False, server_default="0.8"),
        sa.Column("prompt_version", sa.Text, nullable=False, server_default="'v1'"),
        sa.Column("needs_review", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "manual_override", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column(
            "generated_at", sa.DateTime, nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column("raw_response", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("commander_profiles")
