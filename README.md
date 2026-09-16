# wilkr

Personal, self-hosted, segments-first fitness platform. See `docs/` for the
full project charter and context; not yet a running app — Phase 0 (repo
skeleton: Docker Compose, FastAPI, Alembic) hasn't started.

## Layout

- `docs/` — planning docs (charter, context, ERD). Filenames still carry the
  project's original working title, "OpenFit"; content is otherwise current.
- `schema/` — the schema as DBML (`wilkr.dbml`, hand-editable source of
  truth) and its generated PostgreSQL DDL (`wilkr.postgres.sql`). View the
  diagram by pasting `wilkr.dbml` into [dbdiagram.io](https://dbdiagram.io).
