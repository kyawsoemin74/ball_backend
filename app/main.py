import asyncio
import logging
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
from app.api.auth import router as auth_router
from app.api.socket import router as socket_router
from app.api.uploads import router as uploads_router
from app.api.admin_leagues import router as admin_leagues_router
from sqlalchemy import text
from app.db import async_session, engine
from app.admin import setup_admin
from app.monitoring import MonitoringMiddleware, metrics_router, POSTGRES_UP, REDIS_UP, start_worker_metrics_server
from app.redis import sync_redis
from app.services.notification import notification_worker
from app.services.socket_service import broker as redis_broker
from scheduler_service import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start auxiliary background services with the FastAPI application lifecycle.
    start_worker_metrics_server(8001)
    start_scheduler()
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
        stop_scheduler()


app = FastAPI(
    title="Fover Backend API",
    description="Football data management API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Production မှာ တကယ်သုံးမည့် Domain ကိုသာ ပြောင်းလဲရန်
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


async def _check_postgres() -> bool:
    async with async_session() as db:
        try:
            await db.execute(text("SELECT 1"))
            POSTGRES_UP.set(1)
            return True
        except Exception:
            POSTGRES_UP.set(0)
            return False


def _check_redis() -> bool:
    try:
        healthy = sync_redis.ping()
        REDIS_UP.set(1 if healthy else 0)
        return bool(healthy)
    except Exception:
        REDIS_UP.set(0)
        return False


@app.get("/health/live")
def health_live():
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready():
    postgres_ok = await _check_postgres()
    redis_ok = _check_redis()
    status = "ready" if postgres_ok and redis_ok else "unhealthy"
    return JSONResponse(
        status_code=200 if status == "ready" else 503,
        content={"status": status, "postgres": postgres_ok, "redis": redis_ok},
    )