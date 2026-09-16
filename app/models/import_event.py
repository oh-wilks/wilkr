import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Identity, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ImportEvent(Base):
    __tablename__ = "import_events"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    source: Mapped[str] = mapped_column(
        String, nullable=False, comment="strava_import, garmin_sync, manual"
    )
    status: Mapped[str] = mapped_column(
        String, nullable=False, comment="pending, success, failed, duplicate"
    )
    activity_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("activities.id"),
        comment="nullable — set once the import succeeds",
    )
    error_message: Mapped[str | None] = mapped_column(Text, comment="nullable")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "source IN ('strava_import', 'garmin_sync', 'manual')",
            name="ck_import_events_source",
        ),
        CheckConstraint(
            "status IN ('pending', 'success', 'failed', 'duplicate')",
            name="ck_import_events_status",
        ),
    )
