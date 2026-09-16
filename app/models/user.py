import datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    weight_kg: Mapped[float | None] = mapped_column(
        comment="for TSS, power-to-weight"
    )
    height_cm: Mapped[float | None] = mapped_column()
    birth_date: Mapped[datetime.date | None] = mapped_column(
        Date, comment="for age-based HR zones"
    )
    resting_hr: Mapped[int | None] = mapped_column(Integer)
    max_hr: Mapped[int | None] = mapped_column(Integer)
    ftp_watts: Mapped[int | None] = mapped_column(Integer, comment="cycling, nullable")
    timezone: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class UserPreferences(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        primary_key=True,
        comment="shared PK/FK — one-to-one with users",
    )
    unit_system: Mapped[str] = mapped_column(
        String,
        nullable=False,
        server_default=text("'metric'::character varying"),
        comment="metric or imperial, display only",
    )
    default_sport_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sports.id"), comment="nullable"
    )
    theme: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'system'::character varying")
    )
    extra: Mapped[dict | None] = mapped_column(
        JSONB, comment="future settings, no migration needed"
    )

    __table_args__ = (
        CheckConstraint(
            "unit_system IN ('metric', 'imperial')",
            name="ck_user_preferences_unit_system",
        ),
    )
