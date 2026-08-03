"""Add event_reaction_notes and event_price_reactions tables.

Revision ID: 0010_event_log
Revises: 0009_cb_docs
Create Date: 2026-07-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0010_event_log"
down_revision = "0009_cb_docs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_reaction_notes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "indicator_release_id",
            sa.BigInteger(),
            sa.ForeignKey("indicator_releases.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("forecast_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("actual_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("previous_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("manual_notes", sa.Text(), nullable=True),
        sa.Column("ai_interpretation", sa.Text(), nullable=True),
        sa.Column("ai_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "event_price_reactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.BigInteger(),
            sa.ForeignKey("event_reaction_notes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("instrument", sa.String(10), nullable=False),
        sa.Column("horizon", sa.String(4), nullable=False),
        sa.Column("raw_price", sa.Numeric(12, 6), nullable=True),
        sa.Column("pip_change", sa.Numeric(12, 2), nullable=True),
        sa.Column("pct_change", sa.Numeric(10, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "event_id", "instrument", "horizon", name="uq_event_price_reactions_cell"
        ),
    )
    op.create_index(
        "ix_event_price_reactions_event",
        "event_price_reactions",
        ["event_id"],
    )


def downgrade() -> None:
    op.drop_table("event_price_reactions")
    op.drop_table("event_reaction_notes")
