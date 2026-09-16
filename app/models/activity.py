import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    sport_id: Mapped[int] = mapped_column(Integer, ForeignKey("sports.id"), nullable=False)
    equipment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("equipment.id"), comment="nullable"
    )
    source: Mapped[str] = mapped_column(
        String, nullable=False, comment="strava_import, garmin_sync, manual"
    )
    external_id: Mapped[str] = mapped_column(
        String, nullable=False, comment="dedupe key from source"
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    moving_time_s: Mapped[int] = mapped_column(Integer, nullable=False)
    elapsed_time_s: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_m: Mapped[float] = mapped_column(nullable=False)
    elevation_gain_m: Mapped[float | None] = mapped_column()
    avg_hr: Mapped[int | None] = mapped_column(Integer)
    max_hr: Mapped[int | None] = mapped_column(
        Integer,
        comment=(
            "peak HR reached during this activity — distinct from "
            "users.max_hr, the training-zone ceiling"
        ),
    )
    calories_kcal: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index(
            "uq_activities_source_external_id", "source", "external_id", unique=True
        ),
        CheckConstraint(
            "source IN ('strava_import', 'garmin_sync', 'manual')",
            name="ck_activities_source",
        ),
    )
