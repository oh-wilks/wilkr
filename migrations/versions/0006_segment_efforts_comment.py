"""segment_efforts: update table comment for cascade delete note

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-18

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_OLD_COMMENT = (
    "PR status is computed on read — RANK() OVER (PARTITION BY segment_id, "
    "activity.user_id ORDER BY elapsed_time_s) — not stored, so "
    "backfilled/out-of-order imports can never leave a stale is_pr flag."
)
_NEW_COMMENT = (
    _OLD_COMMENT + " segment_id FK is ON DELETE CASCADE — efforts are a "
    "deterministic computation over (segment geometry × existing "
    "activities), reproducible via recreate + rescan, not primary data."
)


def upgrade() -> None:
    op.create_table_comment(
        "segment_efforts", _NEW_COMMENT, existing_comment=_OLD_COMMENT
    )


def downgrade() -> None:
    op.create_table_comment(
        "segment_efforts", _OLD_COMMENT, existing_comment=_NEW_COMMENT
    )
