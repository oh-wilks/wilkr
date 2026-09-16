from sqlalchemy import CheckConstraint, ForeignKey, Identity, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    activity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("activities.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(
        String, nullable=False, comment="heart_rate, elevation, speed, cadence, power"
    )
    data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, comment="time-series array"
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('heart_rate', 'elevation', 'speed', 'cadence', 'power')",
            name="ck_streams_type",
        ),
    )
