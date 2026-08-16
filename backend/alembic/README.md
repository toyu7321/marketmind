# Migration baseline

The initial SQLAlchemy metadata is idempotently created at startup for zero-friction demo use. In production, initialize Alembic against `app.database.Base.metadata` and generate reviewed revisions before schema changes.
