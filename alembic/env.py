"""
Alembic Migration Environment Configuration
============================================
This file handles the Alembic migration setup for the Fover Backend project.

Key Features:
- Auto-generates migrations from SQLAlchemy model changes
- Supports both online (direct DB connection) and offline (SQL script) modes
- Environment-based database URL configuration
- Production-grade autogeneration with comparison of types and defaults
- Comprehensive model imports for migration detection
"""

import os
import sys
from logging.config import fileConfig

# Ensure the app module is in the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import the project's Base and all models for Alembic autogeneration
from app.db import Base
from app.models import (
    User, 
    Match, 
    League, 
    Team, 
    Standings, 
    Odds, 
    Ad, 
    News, 
    MatchEvent, 
    MatchH2H, 
    MatchLineup
)

from sqlalchemy import engine_from_config, pool, MetaData
from alembic import context

# Configure logging
config = context.config
fileConfig(config.config_file_name)

# ============================================================================
# PRODUCTION-GRADE DATABASE URL CONFIGURATION
# ============================================================================
# Priority 1: Environment variable DATABASE_URL (used in production/Docker)
# Priority 2: alembic.ini sqlalchemy.url (fallback for local development)
database_url = os.getenv("DATABASE_URL")

if database_url:
    # Convert postgres:// to postgresql:// for SQLAlchemy 1.4+
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    
    config.set_main_option("sqlalchemy.url", database_url)
    print(f"✓ Using DATABASE_URL from environment")
else:
    database_url = config.get_main_option("sqlalchemy.url")
    print(f"✓ Using sqlalchemy.url from alembic.ini")

# Set target metadata for autogeneration
target_metadata = Base.metadata


# ============================================================================
# OFFLINE MIGRATION MODE (Generate SQL script without DB connection)
# ============================================================================
def run_migrations_offline():
    """
    Run migrations in 'offline' mode.
    Generates migration SQL without connecting to the database.
    Useful for environments without direct DB access or for review before applying.
    """
    url = config.get_main_option("sqlalchemy.url")
    
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Production-grade comparison options
        compare_type=True,              # Detect column type changes
        compare_server_default=True,    # Detect server default changes
        render_as_batch=True,           # Better SQLite compatibility (though we use PostgreSQL)
    )

    with context.begin_transaction():
        context.run_migrations()


# ============================================================================
# ONLINE MIGRATION MODE (Direct DB connection execution)
# ============================================================================
def run_migrations_online():
    """
    Run migrations in 'online' mode.
    Directly connects to database and executes migrations.
    This is the standard mode for development and production deployments.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Production-grade comparison options for autogeneration
            compare_type=True,              # Detect column type changes (important for SQLAlchemy)
            compare_server_default=True,    # Detect server defaults like func.now()
            render_as_batch=True,           # Better DDL generation for some dialects
        )

        with context.begin_transaction():
            context.run_migrations()


# ============================================================================
# EXECUTION ENTRY POINT
# ============================================================================
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
