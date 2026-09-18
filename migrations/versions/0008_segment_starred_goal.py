"""segments: add starred flag and goal_time_s

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-18

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "segments",
        sa.Column(
            "starred",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment=(
                "Personal shortlist flag, not a signal to anyone else — "
                "wilkr is single-user."
            ),
        ),
    )
    op.add_column(
        "segments",
        sa.Column(
            "goal_time_s",
            sa.Integer(),
            nullable=True,
            comment=(
                "Nullable — a user-set target elapsed time, compared "
                "against PR/best-this-year on segment detail."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("segments", "goal_time_s")
    op.drop_column("segments", "starred")
