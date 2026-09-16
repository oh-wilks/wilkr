from geoalchemy2 import Geometry
from sqlalchemy import ForeignKey, Identity, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    activity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("activities.id"), unique=True, nullable=False
    )
    geom: Mapped[str] = mapped_column(
        Geometry(geometry_type="LINESTRINGZ", srid=4326),
        nullable=False,
        comment="SRID 4326",
    )
