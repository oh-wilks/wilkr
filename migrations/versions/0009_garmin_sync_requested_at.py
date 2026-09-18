"""garmin_sync_state: add sync_requested_at for the manual "Sync now" button

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-18

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "garmin_sync_state",
        sa.Column(
            "sync_requested_at",
            sa.DateTime(),
            nullable=True,
            comment=(
                "nullable — set by the web UI's 'Sync now' button, cleared "
                "by the garmin-sync loop once it picks the request up and "
                "runs. The loop polls this on a short interval (see "
                "POLL_INTERVAL_S in app/importers/garmin.py) separately "
                "from its normal GARMIN_SYNC_INTERVAL_S cadence, since "
                "garmin-sync runs as its own container/process — this DB "
                "column is the coordination mechanism between the two, "
                "not a message queue."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("garmin_sync_state", "sync_requested_at")
