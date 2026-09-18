import datetime

from sqlalchemy import CheckConstraint, DateTime, Identity, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GarminSyncState(Base):
    """Single row (this is a single-user app) — the cursor + health record
    for the Garmin sync job. Deliberately not MAX(activities.started_at):
    that breaks on backdated/late-syncing device data and only advances
    when a new activity actually exists, letting the required lookback
    window creep unboundedly during quiet stretches."""

    __tablename__ = "garmin_sync_state"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    last_synced_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime,
        comment=(
            "nullable until the first run completes — advances to the run's "
            "own start time on every run, regardless of whether new "
            "activities were found"
        ),
    )
    last_run_status: Mapped[str | None] = mapped_column(String)
    last_error_message: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    sync_requested_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime,
        comment=(
            "nullable — set by the web UI's 'Sync now' button, cleared by "
            "the garmin-sync loop once it picks the request up and runs. "
            "The loop polls this on a short interval (see POLL_INTERVAL_S "
            "in app/importers/garmin.py) separately from its normal "
            "GARMIN_SYNC_INTERVAL_S cadence, since garmin-sync runs as its "
            "own container/process — this DB column is the coordination "
            "mechanism between the two, not a message queue."
        ),
    )

    __table_args__ = (
        CheckConstraint(
            "last_run_status IN ('success', 'failed')",
            name="ck_garmin_sync_state_last_run_status",
        ),
    )
