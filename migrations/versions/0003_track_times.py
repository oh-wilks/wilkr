"""add tracks.times

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-16

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tracks",
        sa.Column(
            "times",
            sa.ARRAY(sa.DateTime()),
            nullable=True,
            comment=(
                "nullable — one timestamp per geom vertex, same order. Lets "
                "segment matching map a located position along the track "
                "back to a real timestamp; geom alone has no time, and the "
                "streams table's per-type series aren't guaranteed 1:1 with "
                "every vertex. NULL for tracks imported before this column "
                "existed until backfilled (see "
                "app/importers/backfill_track_times.py)."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("tracks", "times")
