"""add garmin_sync_state

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-16

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "garmin_sync_state",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "last_synced_at",
            sa.DateTime(),
            nullable=True,
            comment=(
                "nullable until the first run completes — advances to the "
                "run's own start time on every run, regardless of whether "
                "new activities were found"
            ),
        ),
        sa.Column("last_run_status", sa.String(), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "last_run_status IN ('success', 'failed')",
            name="ck_garmin_sync_state_last_run_status",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("garmin_sync_state_pkey")),
    )


def downgrade() -> None:
    op.drop_table("garmin_sync_state")
