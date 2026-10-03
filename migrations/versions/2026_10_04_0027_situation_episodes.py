"""Persist deterministic situation episodes."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0027_situation_episodes"
down_revision = "0026_country_fiscal_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "situation_episodes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("situation_id", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(8), nullable=False),
        sa.Column("scope_key", sa.String(32), nullable=False),
        sa.Column("started_at", sa.Date(), nullable=False),
        sa.Column("ended_at", sa.Date(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("scope IN ('currency', 'pair')", name="ck_situation_episode_scope"),
        sa.UniqueConstraint("situation_id", "scope", "scope_key", "started_at", name="uq_situation_episode_start"),
    )
    op.create_index("ix_situation_episode_active", "situation_episodes", ["situation_id", "scope_key", "ended_at"])


def downgrade() -> None:
    op.drop_index("ix_situation_episode_active", table_name="situation_episodes")
    op.drop_table("situation_episodes")
