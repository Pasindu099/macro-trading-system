"""Store pasted whiteboard images."""
from alembic import op
import sqlalchemy as sa

revision = "0049_whiteboard_images"
down_revision = "0048_whiteboard_currencies"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_whiteboard_items_kind", "whiteboard_items", type_="check")
    op.create_check_constraint("ck_whiteboard_items_kind", "whiteboard_items",
                               "kind IN ('widget', 'note', 'thesis', 'image')")
    op.create_table(
        "whiteboard_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("board_id", sa.Integer(), sa.ForeignKey("whiteboards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("bytes", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade():
    op.execute("DELETE FROM whiteboard_items WHERE kind='image'")
    op.drop_table("whiteboard_images")
    op.drop_constraint("ck_whiteboard_items_kind", "whiteboard_items", type_="check")
    op.create_check_constraint("ck_whiteboard_items_kind", "whiteboard_items",
                               "kind IN ('widget', 'note', 'thesis')")
