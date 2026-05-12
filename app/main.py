import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.middleware.cors import CORSMiddleware

from app.api.matches import router as matches_router
from app.api.leagues import router as leagues_router
from app.api.teams import router as teams_router
from app.api.ads import router as ads_router
from app.api.news import router as news_router
from app.services.scheduler import live_scheduler
from app.db import Base, engine

# Import models to ensure they are registered with SQLAlchemy
from app.models import match, league, team, standings, ad, news, odds

logging.basicConfig(level=logging.INFO)
# Create tables immediately when the app imports, including the odds table.
Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup as well to ensure schema exists.
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
    
    docs_url=None,
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

# Include routers
app.include_router(matches_router, prefix="/api")
app.include_router(leagues_router, prefix="/api/leagues", tags=["leagues"])
app.include_router(teams_router, prefix="/api/teams", tags=["teams"])
app.include_router(ads_router, prefix="/api/ads", tags=["ads"])
app.include_router(news_router, prefix="/api/news", tags=["news"])

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Swagger UI",
        # CDN link များကို တိုက်ရိုက်ထည့်ပေးခြင်းဖြင့် Asset ပျောက်တာကို ဖြေရှင်းနိုင်သည်
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )


@app.get("/")
def root():
    return {"message": "Fover Backend API", "status": "running"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}