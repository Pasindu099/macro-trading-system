"""Add release_bundles and event_innovation_scores.

The event innovation layer replaces the ad-hoc full-history-stdev surprise
z-score. Nothing is dropped here: the old scoring lived only in
app/services/release_ledger.py (computed at read time, never persisted), so
there is no legacy table to migrate off.

Revision ID: 0016_event_innovation
Revises: 0015_unverified_entity_flag
Create Date: 2026-08-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0016_event_innovation"
down_revision = "0015_unverified_entity_flag"
branch_labels = None
depends_on = None

decay_bucket = postgresql.ENUM(
    "high_freq_high_revision",
    "high_freq_low_revision",
    "low_freq_structural",
    "meeting_adjacent",
    name="decay_bucket",
    create_type=False,
)


def upgrade() -> None:
    decay_bucket.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "release_bundles",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("bundle_key", sa.Text(), nullable=False),
        sa.Column(
            "country",
            sa.String(2),
            sa.ForeignKey("countries.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("release_date", sa.Date(), nullable=False),
        sa.Column(
            "indicator_ids",
            postgresql.ARRAY(sa.Integer()),
            nullable=False,
            server_default="{}",
        ),
        # The collapsed latent score for the bundle. Not in the original sketch,
        # but a bundle that cannot carry its own score is only a grouping.
        sa.Column("bundle_score", sa.Numeric(10, 6), nullable=True),
        sa.Column("decay_bucket", decay_bucket, nullable=True),
        sa.Column("half_life_days", sa.Numeric(6, 2), nullable=True),
        sa.Column("member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # One bundle per key per day — the natural key the backfill upserts on.
        sa.UniqueConstraint(
            "bundle_key", "release_date", name="uq_release_bundles_key_date"
        ),
    )
    op.create_index(
        "ix_release_bundles_country_date",
        "release_bundles",
        ["country", "release_date"],
    )

    op.create_table(
        "event_innovation_scores",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "release_id",
            sa.BigInteger(),
            sa.ForeignKey("indicator_releases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "bundle_id",
            sa.BigInteger(),
            sa.ForeignKey("release_bundles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "indicator_id",
            sa.Integer(),
            sa.ForeignKey("indicators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("release_date", sa.Date(), nullable=False),
        sa.Column("actual", sa.Numeric(20, 6), nullable=True),
        sa.Column("consensus", sa.Numeric(20, 6), nullable=True),
        sa.Column("surprise_raw", sa.Numeric(20, 6), nullable=True),
        sa.Column("surprise_scale", sa.Numeric(20, 6), nullable=True),
        sa.Column("surprise_normalized", sa.Numeric(10, 6), nullable=True),
        sa.Column("decay_bucket", decay_bucket, nullable=True),
        sa.Column("half_life_days", sa.Numeric(6, 2), nullable=True),
        # Low-impact releases (EODHD impact = low) are stored but not scored,
        # so the impact threshold can be relaxed later without re-ingesting.
        sa.Column(
            "scored", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("release_id", name="uq_event_innovation_release"),
    )
    op.create_index(
        "ix_event_innovation_scores_release_date",
        "event_innovation_scores",
        ["release_date"],
    )
    op.create_index(
        "ix_event_innovation_scores_indicator_date",
        "event_innovation_scores",
        ["indicator_id", "release_date"],
    )
    op.create_index(
        "ix_event_innovation_scores_bundle_id",
        "event_innovation_scores",
        ["bundle_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_event_innovation_scores_bundle_id", table_name="event_innovation_scores"
    )
    op.drop_index(
        "ix_event_innovation_scores_indicator_date",
        table_name="event_innovation_scores",
    )
    op.drop_index(
        "ix_event_innovation_scores_release_date",
        table_name="event_innovation_scores",
    )
    op.drop_table("event_innovation_scores")
    op.drop_index("ix_release_bundles_country_date", table_name="release_bundles")
    op.drop_table("release_bundles")
    decay_bucket.drop(op.get_bind(), checkfirst=True)
