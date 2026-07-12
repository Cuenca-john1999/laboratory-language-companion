from logging.config import fileConfig

from sqlalchemy import pool

from alembic import context
from deutschos_api.core.config import get_settings
from deutschos_api.db.base import Base
from deutschos_api.db.session import make_engine
from deutschos_api.models import *  # noqa: F403

config = context.config
settings = get_settings()
settings.ensure_data_directory()
config.set_main_option("sqlalchemy.url", settings.database_url)
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    # SQLite batch migrations rebuild tables. Disable enforcement only for the
    # migration connection, then verify that no relationship was broken.
    connectable = make_engine(
        settings.database_url, poolclass=pool.NullPool, enable_foreign_keys=False
    )
    with connectable.connect() as connection:
        if connection.dialect.name == "sqlite":
            before = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if before:
                raise RuntimeError(
                    "Existing SQLite foreign-key violations must be repaired before migration."
                )
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()
        if connection.dialect.name == "sqlite":
            after = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if after:
                raise RuntimeError(
                    "Migration produced SQLite foreign-key violations. Stop and restore the "
                    "verified pre-migration backup before continuing."
                )
            connection.commit()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
