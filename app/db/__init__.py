import asyncio
import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# ============================================================================
# Database Configuration - Single Source of Truth
# ============================================================================
# Use environment variable if set otherwise fallback to localhost default
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://fover_user:242374@localhost:5432/fover_db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

_engine = None
_loop_key = None


def _get_running_loop_key() -> int | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return id(loop)


def _build_engine():
    return create_async_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        future=True,
    )


def _get_or_create_engine():
    global _engine, _loop_key
    loop_key = _get_running_loop_key()
    if loop_key is None:
        if _engine is None:
            _engine = _build_engine()
            _loop_key = None
        return _engine

    if _engine is None or _loop_key != loop_key:
        _engine = _build_engine()
        _loop_key = loop_key
    return _engine


class _LoopBoundAsyncSessionFactory:
    def __call__(self, *args, **kwargs):
        return AsyncSession(
            bind=_get_or_create_engine(),
            expire_on_commit=False,
            autoflush=False,
            future=True,
            *args,
            **kwargs,
        )


class _LoopBoundEngineProxy:
    def __getattr__(self, name):
        return getattr(_get_or_create_engine(), name)

    async def dispose(self):
        engine = _get_or_create_engine()
        await engine.dispose()


engine = _LoopBoundEngineProxy()
async_session = _LoopBoundAsyncSessionFactory()
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