import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse


from app.core.config import settings
from app.api.matches import router as matches_router
from app.api.leagues import router as leagues_router
from app.api.home import router as home_router
from app.api.teams import router as teams_router
from app.api.ads import router as ads_router
from app.api.news import router as news_router
from app.api.v2.player_contracts import router as player_v2_router
from app.api.auth import router as auth_router
from app.api.socket import router as socket_router
from app.api.uploads import router as uploads_router
from app.api.admin_leagues import router as admin_leagues_router
from sqlalchemy import text
from app.db import async_session, engine
from app.admin import setup_admin
from app.monitoring import MonitoringMiddleware, metrics_router, refresh_dependency_health
from app.services.notification import notification_worker
from app.services.socket_service import broker as redis_broker

log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_name, logging.INFO)
logging.basicConfig(level=log_level)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.notification_worker_task = asyncio.create_task(notification_worker.start())

    try:
        yield
    finally:
        await notification_worker.stop()
        if hasattr(app.state, "notification_worker_task"):
            app.state.notification_worker_task.cancel()
            try:
                await app.state.notification_worker_task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title="Fover Backend API",
    description="Football data management API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENABLE_API_DOCS else None,
    redoc_url="/redoc" if settings.ENABLE_API_DOCS else None,
    openapi_url="/api/openapi.json" if settings.ENABLE_API_DOCS else None
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session Middleware (Required for SQLAdmin authentication)
app.add_middleware(SessionMiddleware, secret_key=settings.JWT_SECRET_KEY)

# Initialize Admin Panel
setup_admin(app, engine)

# Monitoring middleware and metrics
app.add_middleware(MonitoringMiddleware)
if settings.ENABLE_API_METRICS:
    app.include_router(metrics_router)

# WebSocket router
app.include_router(socket_router)

# Include routers
app.include_router(matches_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(home_router, prefix="/api", tags=["home"])
app.include_router(admin_leagues_router, prefix="/api/admin", tags=["admin"])
app.include_router(leagues_router, prefix="/api/leagues", tags=["leagues"])
app.include_router(teams_router, prefix="/api/teams", tags=["teams"])
app.include_router(ads_router, prefix="/api/ads", tags=["ads"])
app.include_router(news_router, prefix="/api/news", tags=["news"])
app.include_router(uploads_router, prefix="/api", tags=["uploads"])
app.include_router(player_v2_router, prefix="/api/v2")


@app.on_event("startup")
async def start_websocket_broker() -> None:
    app.state.websocket_broker_task = asyncio.create_task(redis_broker.start())


@app.on_event("shutdown")
async def stop_websocket_broker() -> None:
    if hasattr(app.state, "websocket_broker_task"):
        await redis_broker.stop()
        app.state.websocket_broker_task.cancel()

@app.get("/")
def root():
    return {"message": "Fover Backend API", "status": "running"}


@app.get("/health")
def health_check():
    return {"status": "alive"}


@app.get("/health/live")
def health_live():
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready():
    postgres_ok, redis_ok = await refresh_dependency_health()
    status = "ready" if postgres_ok and redis_ok else "unhealthy"
    return JSONResponse(
        status_code=200 if status == "ready" else 503,
        content={"status": status, "postgres": postgres_ok, "redis": redis_ok},
    )