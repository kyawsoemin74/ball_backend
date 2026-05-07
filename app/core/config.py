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

    # Supported leagues for daily sync (example: Premier League, La Liga, Serie A)
    SUPPORTED_LEAGUES: list[int] = [39, 140, 135] # Example IDs

    # API Key for your backend's authentication
    API_KEY: str = Field(..., description="Secret key for X-API-KEY header validation")

settings = Settings()