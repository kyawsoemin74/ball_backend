import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.getcwd())))
# Import the project's Base so Alembic can autogenerate against SQLAlchemy metadata
from app.db import Base
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# အောက်ပါအတိုင်း MatchLineup ကို import စာရင်းထဲ ထည့်ပါ
from app.models import User, Match, League, Team, Standings, Odds, Ad, News, MatchEvent, MatchH2H, MatchLineup

config = context.config
fileConfig(config.config_file_name)

# Use DATABASE_URL from environment if provided
database_url = os.getenv("DATABASE_URL")
if database_url:
    print(f"DEBUG: Using DATABASE_URL from environment: {database_url}")
    config.set_main_option("sqlalchemy.url", database_url)
else:
    print(f"DEBUG: Using sqlalchemy.url from alembic.ini: {config.get_main_option('sqlalchemy.url')}")

target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
