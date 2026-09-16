import datetime

from geoalchemy2 import Geometry
from sqlalchemy import ARRAY, DateTime, ForeignKey, Identity, Integer
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
    times: Mapped[list[datetime.datetime] | None] = mapped_column(
        ARRAY(DateTime),
        comment=(
            "nullable — one timestamp per geom vertex, same order. Lets "
            "segment matching map a located position along the track back "
            "to a real timestamp; geom alone has no time, and the streams "
            "table's per-type series aren't guaranteed 1:1 with every "
            "vertex. NULL for tracks imported before this column existed "
            "until backfilled (see app/importers/backfill_track_times.py)."
        ),
    )
