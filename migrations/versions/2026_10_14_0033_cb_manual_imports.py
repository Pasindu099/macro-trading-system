"""Audit of manual central-bank imports for blocked / PDF-only sources (Step 12 decision)."""

from alembic import op
import sqlalchemy as sa

revision = "0033_cb_manual_imports"
down_revision = "0032_cb_projection_point_stats"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cb_manual_imports",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("bank", sa.String(8), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),          # projections | statements | speeches
        sa.Column("method", sa.String(16), nullable=False),        # file | manual_entry
        sa.Column("source", sa.Text(), nullable=True),             # uploaded file name
        sa.Column("round_date", sa.Date(), nullable=True),
        sa.Column("rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_by", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('projections', 'statements', 'speeches')", name="ck_cb_manual_imports_kind"),
    )
    op.create_index("ix_cb_manual_imports_bank_kind", "cb_manual_imports", ["bank", "kind", "imported_at"])


def downgrade():
    op.drop_index("ix_cb_manual_imports_bank_kind", table_name="cb_manual_imports")
    op.drop_table("cb_manual_imports")
