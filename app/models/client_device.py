import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Identity, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ClientDevice(Base):
    __tablename__ = "client_devices"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    client_key: Mapped[str] = mapped_column(
        String,
        unique=True,
        nullable=False,
        comment=(
            "random ID generated once by the device and stored locally — "
            "not a credential, just a stable self-reported identity so "
            "repeat visits upsert the same row"
        ),
    )
    label: Mapped[str | None] = mapped_column(
        String,
        comment='friendly device name, e.g. "Ivan iPhone 15" — self-reported, editable later',
    )
    platform: Mapped[str] = mapped_column(
        String, nullable=False, comment="ios, web, other"
    )
    app_version: Mapped[str | None] = mapped_column(
        String, comment="nullable — self-reported client version"
    )
    first_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "platform IN ('ios', 'web', 'other')",
            name="ck_client_devices_platform",
        ),
        {
            "comment": (
                "Informational only, not an auth mechanism — Tailscale is "
                "the actual access-control layer. All devices share the "
                "one API key; this table just powers a \"what has been "
                "using my library\" dashboard, upserted on each check-in "
                "by client_key."
            )
        },
    )
