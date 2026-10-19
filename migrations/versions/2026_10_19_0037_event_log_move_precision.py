"""Keep entered intraday moves at research-grade decimal precision."""

from alembic import op
import sqlalchemy as sa

revision = "0037_event_log_move_precision"
down_revision = "0036_event_log_moves"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("event_log_moves", "move", existing_type=sa.Numeric(10, 1),
                    type_=sa.Numeric(16, 6), existing_nullable=False)


def downgrade():
    op.alter_column("event_log_moves", "move", existing_type=sa.Numeric(16, 6),
                    type_=sa.Numeric(10, 1), existing_nullable=False)
