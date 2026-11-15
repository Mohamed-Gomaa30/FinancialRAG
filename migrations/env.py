"""Alembic environment: connects Alembic to our settings and table models.

- The database URL comes from core.config (DATABASE_URL in .env), so no
  credentials live in alembic.ini.
- target_metadata = Base.metadata lets `alembic revision --autogenerate`
  compare our table models with the live database.

Run alembic from the project root (where .env and alembic.ini are).
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from core.config import settings  # importable via prepend_sys_path = src in alembic.ini
from core.db import Base
import jobs.tables  # noqa: F401  (registers DocumentRow/JobRow on Base.metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# "%" must be doubled: alembic.ini values go through configparser interpolation,
# and a password containing "%" would otherwise break.
config.set_main_option("sqlalchemy.url", settings().DATABASE_URL.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and apply migrations."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # a one-off command doesn't need a pool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
