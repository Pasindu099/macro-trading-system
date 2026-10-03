"""Add job_watermarks for incremental analytics jobs.

One row per incremental job. ``last_success_at`` is the START time of the
last successful run: anything retrieved after it (minus a small overlap) is
picked up by the next run.

Revision ID: 0021_job_watermarks
Revises: 0020_fx_spot_observations
Create Date: 2026-10-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0021_job_watermarks"
down_revision = "0020_fx_spot_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_watermarks",
        sa.Column("job_name", sa.Text(), primary_key=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("job_watermarks")
