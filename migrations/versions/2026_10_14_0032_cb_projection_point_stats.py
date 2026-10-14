"""Allow single-point projections and rate assumptions in cb_projection_values (Step 12 Part B)."""

from alembic import op

revision = "0032_cb_projection_point_stats"
down_revision = "0031_event_log_entries"
branch_labels = None
depends_on = None

OLD = "stat IN ('median', 'ct_low', 'ct_high', 'range_low', 'range_high')"
NEW = "stat IN ('median', 'ct_low', 'ct_high', 'range_low', 'range_high', 'point', 'assumption')"


def upgrade():
    op.drop_constraint("ck_cb_projection_values_stat", "cb_projection_values", type_="check")
    op.create_check_constraint("ck_cb_projection_values_stat", "cb_projection_values", NEW)


def downgrade():
    op.execute("DELETE FROM cb_projection_values WHERE stat IN ('point', 'assumption')")
    op.drop_constraint("ck_cb_projection_values_stat", "cb_projection_values", type_="check")
    op.create_check_constraint("ck_cb_projection_values_stat", "cb_projection_values", OLD)
