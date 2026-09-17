"""segment_efforts: cascade delete from segments, composite index

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-18

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres has no ALTER CONSTRAINT for ON DELETE — drop and recreate
    # with the same definition plus CASCADE. Efforts aren't primary data:
    # they're a deterministic computation over (segment geometry ×
    # existing activities), reproducible via recreate + rescan, so deleting
    # a segment should just take its efforts with it in one statement
    # instead of the two manual ones used by hand all session.
    op.drop_constraint(
        "segment_efforts_segment_id_fkey", "segment_efforts", type_="foreignkey"
    )
    op.create_foreign_key(
        "segment_efforts_segment_id_fkey",
        "segment_efforts",
        "segments",
        ["segment_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_segment_efforts_segment_id_elapsed_time_s",
        "segment_efforts",
        ["segment_id", "elapsed_time_s"],
    )


def downgrade() -> None:
    op.drop_index("ix_segment_efforts_segment_id_elapsed_time_s", table_name="segment_efforts")
    op.drop_constraint(
        "segment_efforts_segment_id_fkey", "segment_efforts", type_="foreignkey"
    )
    op.create_foreign_key(
        "segment_efforts_segment_id_fkey",
        "segment_efforts",
        "segments",
        ["segment_id"],
        ["id"],
    )
