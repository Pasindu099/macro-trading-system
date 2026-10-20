"""Record validated BoJ outlook source dates and URLs per projection round."""

from alembic import op
import sqlalchemy as sa

revision = "0045_boj_projection_sources"
down_revision = "0044_cb_votes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cb_projection_round_sources",
        sa.Column("bank", sa.String(8), primary_key=True),
        sa.Column("release_date", sa.Date(), primary_key=True),
        sa.Column("source_date", sa.Date(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("pdf_url", sa.Text()),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("source_date <= release_date", name="ck_projection_source_date"),
    )


def downgrade():
    op.drop_table("cb_projection_round_sources")
