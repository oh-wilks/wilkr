import datetime

from sqlalchemy import CheckConstraint, Column, Date, ForeignKey, Identity, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Equipment(Base):
    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(
        String, nullable=False, comment="e.g. Pegasus 40 (right)"
    )
    type: Mapped[str] = mapped_column(String, nullable=False, comment="shoe, bike, ski, other")
    brand: Mapped[str | None] = mapped_column(String)
    model: Mapped[str | None] = mapped_column(String)
    purchased_at: Mapped[datetime.date | None] = mapped_column(Date)
    retired_at: Mapped[datetime.date | None] = mapped_column(
        Date,
        comment=(
            "nullable — NULL means active; a date means retired. "
            "No separate is_active flag."
        ),
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('shoe', 'bike', 'ski', 'other')",
            name="ck_equipment_type",
        ),
    )


equipment_sports = Table(
    "equipment_sports",
    Base.metadata,
    Column("equipment_id", Integer, ForeignKey("equipment.id"), primary_key=True),
    Column("sport_id", Integer, ForeignKey("sports.id"), primary_key=True),
)
