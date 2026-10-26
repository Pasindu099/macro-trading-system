"""Limit whiteboards to the eight supported currency desks."""

from alembic import op
import sqlalchemy as sa

from app.settings import SUPPORTED_CURRENCIES

revision = "0048_whiteboard_currencies"
down_revision = "0047_whiteboards"
branch_labels = None
depends_on = None


def upgrade():
    allowed = ", ".join(f"'{currency}'" for currency in SUPPORTED_CURRENCIES)
    op.create_check_constraint(
        "ck_whiteboards_currency", "whiteboards", f"currency IN ({allowed})"
    )


def downgrade():
    op.drop_constraint("ck_whiteboards_currency", "whiteboards", type_="check")
