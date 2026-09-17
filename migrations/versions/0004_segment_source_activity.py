"""add segments.source_activity_id

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-17

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "segments",
        sa.Column(
            "source_activity_id",
            sa.Integer(),
            sa.ForeignKey("activities.id"),
            nullable=True,
            comment=(
                "nullable — the activity a segment's start/end points were "
                "originally picked from. Needed to re-render the creation "
                "slider for editing a segment's endpoints, and to derive "
                "elevation stats on the fly from the source track's "
                "LINESTRINGZ geometry (segments.geom itself is flattened to "
                "2D). NULL for segments created before this column existed."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("segments", "source_activity_id")
