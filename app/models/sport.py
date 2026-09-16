from sqlalchemy import CheckConstraint, Identity, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Sport(Base):
    __tablename__ = "sports"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(
        String,
        unique=True,
        nullable=False,
        comment="running, road_cycling, mtb, alpine_ski, nordic_ski, hike, swim",
    )
    category: Mapped[str] = mapped_column(
        String, nullable=False, comment="endurance, strength, other"
    )

    __table_args__ = (
        CheckConstraint(
            "category IN ('endurance', 'strength', 'other')",
            name="ck_sports_category",
        ),
    )
