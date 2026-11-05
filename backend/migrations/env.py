from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.database import Base
import app.models  # noqa: F401 - registers all tables in Base.metadata

config = context.config
if config.config_file_name:
    # Alembic is also executed in-process by readiness and regression tests.
    # Keep application loggers alive instead of letting ``fileConfig`` disable
    # every logger that is not declared in alembic.ini.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

database_url = os.getenv("DATABASE_URL")
if database_url:
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline():
    # Catalog/data checks in these reviewed revisions cannot be represented by
    # a SQL-only MockConnection. Fail before emitting a partial migration.
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(config)
    guarded = {"c2d3e4f5a6b7", "c3d4e5f6a7b8"}
    revisions = script.iterate_revisions(
        context.get_revision_argument(), context.get_starting_revision_argument()
    )
    if any(revision.revision in guarded for revision in revisions):
        raise RuntimeError("Offline SQL export requires live catalog preflight for guarded revisions")
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
