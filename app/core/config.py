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
    SUPPORTED_LEAGUES: list[int] = [39, 140, 135, 186, 128, 344, 71, 242, 233, 268, 17, 
    18,    # AFC Cup
    292,   # Saudi Pro League
    307,   # UAE Pro League
    301,   # Qatar Stars League
    302,   # Bahrain Premier League
    304,   # Oman Professional League
    305,   # Kuwait Premier League
    306,   # Jordan League
    308,   # Iraqi Premier League
    310,   # Iran Pro League
    311,   # Uzbekistan Super League
    312,   # Tajikistan Vysshaya Liga
    313,   # Turkmenistan Yokary Liga
    314,   # Kyrgyzstan Premier League
    315,   # Kazakhstan Premier League
    316,   # Indian Super League
    323,   # Indonesia Liga 1
    324,   # Malaysia Super League
    325,   # Singapore Premier League
    326,   # Thailand League 1
    327,   # Vietnam V.League 1
    328,   # Philippines Football League
    330,   # Chinese Super League
    331,   # Japanese J1 League
    332,   # South Korea K League 1
    333,   # Australia A-League
    334,   # Myanmar National League
    337,   # Bangladesh Premier League
] 
    
    API_KEY: str = Field(..., description="Secret key for X-API-KEY header validation")

settings = Settings()