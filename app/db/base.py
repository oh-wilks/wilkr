from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Matches PostgreSQL's own default naming for unnamed constraints, since the
# initial migration creates them unnamed via raw SQL and Postgres auto-names
# them this way. Without this, autogenerate sees every anonymous FK/UK as a
# name mismatch (drop + add) instead of "unchanged".
NAMING_CONVENTION = {
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "pk": "%(table_name)s_pkey",
    "uq": "%(table_name)s_%(column_0_name)s_key",
    "ck": "%(table_name)s_%(column_0_name)s_check",
    "ix": "ix_%(column_0_label)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
