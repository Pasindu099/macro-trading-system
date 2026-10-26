"""Store currency whiteboards, their items, and links."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0047_whiteboards"
down_revision = "0046_policy_model_daily"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "whiteboards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("viewport", JSONB(), nullable=False,
                  server_default=sa.text("'{\"x\": 0, \"y\": 0, \"zoom\": 1}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("currency", "name", name="uq_whiteboards_currency_name"),
    )
    op.create_table(
        "whiteboard_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("board_id", sa.Integer(), sa.ForeignKey("whiteboards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("widget_type", sa.Text()),
        sa.Column("config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("x", sa.Integer(), nullable=False),
        sa.Column("y", sa.Integer(), nullable=False),
        sa.Column("w", sa.Integer(), nullable=False),
        sa.Column("h", sa.Integer(), nullable=False),
        sa.Column("z", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("kind IN ('widget', 'note', 'thesis')", name="ck_whiteboard_items_kind"),
    )
    op.create_table(
        "whiteboard_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("board_id", sa.Integer(), sa.ForeignKey("whiteboards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_item_id", sa.Integer(), sa.ForeignKey("whiteboard_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_item_id", sa.Integer(), sa.ForeignKey("whiteboard_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.Text()),
    )


def downgrade():
    op.drop_table("whiteboard_links")
    op.drop_table("whiteboard_items")
    op.drop_table("whiteboards")
