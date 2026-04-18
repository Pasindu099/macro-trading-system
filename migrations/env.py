"""Alembic migration environment.

This file is executed by Alembic at migration time. It's responsible for
connecting to the database using our app settings, and running migrations
either online (against a live DB) or offline (generating SQL).

Key differences from the stock Alembic template:
1. Database URL comes from app.settings, not alembic.ini.
2. Uses async SQLAlchemy engine (our app is async throughout).
3. Imports our SQLAlchemy Base so autogenerate can detect model changes.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.db.models import Base  # noqa: F401 — imported for metadata side effect
from app.settings import get_settings

# Alembic Config object, provides access to values in alembic.ini.
config = context.config

# Set up Python logging per alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject our database URL dynamically so it comes from .env, not alembic.ini.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# target_metadata is used by Alembic's autogenerate feature. When you change
# a SQLAlchemy model and run `alembic revision --autogenerate`, Alembic
# diffs the models against the DB and generates a migration.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine, so we
    don't even need a DBAPI to be available. Calls to context.execute() here
    emit the given string to the script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations against an existing connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Include schema name in autogenerate output (we use default schema,
        # but this keeps diffs explicit).
        include_schemas=False,
        # Compare column types so autogenerate detects type changes.
        compare_type=True,
        # Compare server defaults so autogenerate detects default changes.
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in online mode using an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connected to a database)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()