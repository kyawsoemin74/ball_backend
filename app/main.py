from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from app.api.matches import router as matches_router
from app.services.scheduler import live_scheduler
from app.db.database import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup
    Base.metadata.create_all(bind=engine)
    
    # Startup: Start the live update scheduler
    live_scheduler.start()
    yield
    # Shutdown: Stop the live update scheduler
    live_scheduler.stop()


app = FastAPI(
    title="Fover Backend API",
    description="Football data management API",
    version="1.0.0",
    lifespan=lifespan,
    root_path="/api",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(matches_router, prefix="/api")


@app.get("/")
def root():
    return {"message": "Fover Backend API", "status": "running"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}