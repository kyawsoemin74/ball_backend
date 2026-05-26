from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import os

# ============================================================================
# Database Configuration - Single Source of Truth
# ============================================================================
# Use environment variable if set otherwise fallback to localhost default
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://fover_user:242374@localhost:5432/fover_db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# Create async engine with connection pooling
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
)

# Create AsyncSession class for session management
async_session = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    future=True,
)

# Alias for compatibility with legacy sync naming if code still imports SessionLocal
AsyncSessionLocal = async_session

# Create Base class for ORM models
Base = declarative_base()


async def get_db():
    """
    Dependency function for FastAPI routes to get an async database session.
    Ensures proper cleanup after request completion.
    """
    async with async_session() as db:
        yield db