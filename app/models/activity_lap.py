import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Identity, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ActivityLap(Base):
    __tablename__ = "activity_laps"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    activity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("activities.id"), nullable=False
    )
    lap_index: Mapped[int] = mapped_column(Integer, nullable=False)
    lap_type: Mapped[str] = mapped_column(
        String, nullable=False, comment="run, lift, active, rest, interval"
    )
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    elapsed_time_s: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_m: Mapped[float | None] = mapped_column()
    elevation_change_m: Mapped[float | None] = mapped_column()
    avg_speed_mps: Mapped[float | None] = mapped_column()
    max_speed_mps: Mapped[float | None] = mapped_column()

    __table_args__ = (
        CheckConstraint(
            "lap_type IN ('run', 'lift', 'active', 'rest', 'interval')",
            name="ck_activity_laps_lap_type",
        ),
    )
