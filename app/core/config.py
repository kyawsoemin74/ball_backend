from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    Uses pydantic-settings for robust configuration management.
    """
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # API-Football settings (assuming these are also in your .env)
    FOOTBALL_API_BASE_URL: str = "https://v3.football.api-sports.io"
    FOOTBALL_API_KEY: str

    API_KEY: Optional[str] = Field(None, description="Legacy API key setting, not used by JWT auth")
    JWT_SECRET_KEY: str = Field(..., description="Secret key used to sign JWT tokens")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_CACHE_PREFIX: str = "fover"
    REDIS_TTL_LIVE_MATCHES: int = 20
    REDIS_TTL_LEAGUE_TEAM: int = 3600
    REDIS_TTL_STANDINGS: int = 21600
    REDIS_TTL_LINEUP: int = 21600
    REDIS_TTL_ODDS: int = 3000
    REDIS_TTL_NEWS: int = 120
    LINEUP_REFRESH_COOLDOWN_SECONDS: int = 900
    GOOGLE_CLIENT_ID: str

    # Upload settings for admin and public image storage
    NEWS_UPLOAD_DIR: str = "/var/www/fover/uploads/news"
    NEWS_UPLOAD_PUBLIC_URL: str = "https://kyawsoemin.com/uploads/news/"
settings = Settings()