"""Keep Legacy futures-only non-commercials separate from TFF groups."""
from alembic import op
import sqlalchemy as sa

revision = "0050_cot_legacy"
down_revision = "0049_whiteboard_images"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("cot_legacy_positions",
        sa.Column("report_type", sa.Text(), primary_key=True, server_default="legacy_futures_only"),
        sa.Column("report_date", sa.Date(), primary_key=True),
        sa.Column("contract_code", sa.String(10), primary_key=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("long", sa.BigInteger(), nullable=False),
        sa.Column("short", sa.BigInteger(), nullable=False),
        sa.Column("spreading", sa.BigInteger()),
        sa.Column("open_interest", sa.BigInteger(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("report_type='legacy_futures_only'", name="ck_cot_legacy_report_type"))
    op.create_index("ix_cot_legacy_currency_date", "cot_legacy_positions", ["currency", "report_date"])


def downgrade():
    op.drop_table("cot_legacy_positions")
