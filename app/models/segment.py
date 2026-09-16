import datetime

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, ForeignKey, Identity, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Segment(Base):
    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    sport_id: Mapped[int] = mapped_column(Integer, ForeignKey("sports.id"), nullable=False)
    geom: Mapped[str] = mapped_column(
        Geometry(geometry_type="LINESTRING", srid=4326),
        nullable=False,
        comment="SRID 4326",
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    sport: Mapped["Sport"] = relationship()


class SegmentEffort(Base):
    __tablename__ = "segment_efforts"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    segment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("segments.id"), nullable=False
    )
    activity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("activities.id"), nullable=False
    )
    elapsed_time_s: Mapped[int] = mapped_column(Integer, nullable=False)
    achieved_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)

    activity: Mapped["Activity"] = relationship()

    __table_args__ = {
        "comment": (
            "PR status is computed on read — RANK() OVER (PARTITION BY "
            "segment_id, activity.user_id ORDER BY elapsed_time_s) — not "
            "stored, so backfilled/out-of-order imports can never leave a "
            "stale is_pr flag."
        )
    }
