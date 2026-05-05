import os
from dotenv import load_dotenv

load_dotenv()

# Football API Configuration
FOOTBALL_API_BASE_URL = "https://v3.football.api-sports.io"
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")

# Supported Leagues (e.g., Premier League, Myanmar National League)
SUPPORTED_LEAGUES = [39, 235, 307,292,169,296]