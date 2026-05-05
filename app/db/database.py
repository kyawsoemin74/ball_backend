from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# PostgreSQL connection string
SQLALCHEMY_DATABASE_URL = "postgresql://fover_user:242374@localhost:5432/fover_db"

# Create engine
engine = create_engine(SQLALCHEMY_DATABASE_URL)

# Create SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create Base class for models
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()