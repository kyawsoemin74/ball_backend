from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# ============================================================================
# Database Configuration - Single Source of Truth
# ============================================================================
# Use environment variable if set, otherwise fallback to localhost default
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://fover_user:242374@localhost:5432/fover_db")

# Create engine with connection pooling
engine = create_engine(DATABASE_URL)

# Create SessionLocal class for session management
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create Base class for ORM models
Base = declarative_base()


def get_db():
    """
    Dependency function for FastAPI routes to get database session.
    Ensures proper cleanup after request completion.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()