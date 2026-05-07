import httpx
import logging
from typing import Optional
from datetime import datetime
from sqlalchemy.orm import Session

from app.models.match import Match
from app.schemas.match import MatchCreate
from app.core.config import settings

# Constants for match statuses
FINISHED_STATUSES = {"FT", "AET", "PEN", "P", "CANC", "ABD", "AWD", "WO"}

logger = logging.getLogger(__name__)

class FootballAPIService:
    def __init__(self):
        self.base_url = settings.FOOTBALL_API_BASE_URL
        self.api_key = settings.FOOTBALL_API_KEY
        self.headers = {
            "x-apisports-key": self.api_key,
            "Content-Type": "application/json"
        }

    async def get_fixtures(self, league: int, season: int) -> Optional[dict]:
        if not self.api_key:
            raise ValueError("FOOTBALL_API_KEY not set in environment variables")
        
        endpoint = f"{self.base_url}/fixtures"
        params = {
            "league": league,
            "season": season
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                
                if data.get("errors"):
                    logger.error(f"API Error: {data['errors']}")
                    
                return data
        except Exception as e:
            logger.error(f"HTTP Connection Error: {e}")
            return None

    async def get_fixtures_by_date(self, target_date: str) -> Optional[dict]:
        """ target_date (YYYY-MM-DD) အလိုက် ပွဲစဉ်များ ဆွဲယူခြင်း """
        endpoint = f"{self.base_url}/fixtures"
        params = {"date": target_date}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                return data
        except Exception as e:
            logger.error(f"Error fetching fixtures for date {target_date}: {e}")
            return None

    async def get_live_fixtures(self) -> Optional[dict]:
        """ Get all currently live fixtures """
        endpoint = f"{self.base_url}/fixtures"
        params = {"live": "all"}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                return data
        except Exception as e:
            logger.error(f"Error fetching live fixtures: {e}")
            return None

    def parse_fixture_to_match(self, fixture: dict) -> Optional[MatchCreate]:
        """ API ကလာတဲ့ ရှုပ်ထွေးတဲ့ JSON ကို MatchCreate Schema အဖြစ် ပြောင်းလဲပေးပါတယ် """
        try:
            f_info = fixture.get("fixture", {})
            f_league = fixture.get("league", {})
            f_teams = fixture.get("teams", {})
            f_goals = fixture.get("goals", {})

            # ISO Format Date ကို Python Datetime အဖြစ်ပြောင်းလဲခြင်း
            date_str = f_info.get("date")
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))

            return MatchCreate(
                fixture_id=f_info.get("id"),
                league_id=f_league.get("id"),
                league_name=f_league.get("name"),
                league_logo=f_league.get("logo"),
                country_name=f_league.get("country"),
                country_logo=f_league.get("flag"),
                match_time=dt,
                status=f_info.get("status", {}).get("short", "NS"),
                elapsed=f_info.get("status", {}).get("elapsed", 0) or 0,
                home_team=f_teams.get("home", {}).get("name"),
                home_team_logo=f_teams.get("home", {}).get("logo"),
                away_team=f_teams.get("away", {}).get("name"),
                away_team_logo=f_teams.get("away", {}).get("logo"),
                home_score=f_goals.get("home") or 0,
                away_score=f_goals.get("away") or 0,
                venue_name=f_info.get("venue", {}).get("name", "Unknown"),
                venue_city=f_info.get("venue", {}).get("city", "Unknown")
            )
        except Exception as e:
            logger.error(f"Parsing error for Fixture ID {fixture.get('fixture', {}).get('id')}: {e}")
            return None

    def _process_sync(self, db: Session, fixtures: list) -> dict:
        """ Upsert logic (Insert if not exists, Update if exists) """
        parsed_matches = []
        for f in fixtures:
            m = self.parse_fixture_to_match(f)
            if m:
                parsed_matches.append(m)
        
        fixture_ids = [m.fixture_id for m in parsed_matches]
        existing_matches_list = db.query(Match).filter(Match.fixture_id.in_(fixture_ids)).all()
        match_map = {m.fixture_id: m for m in existing_matches_list}

        inserted = 0
        updated = 0
        
        for match_create in parsed_matches:
            existing_match = match_map.get(match_create.fixture_id)
            
            if existing_match and existing_match.status not in FINISHED_STATUSES:
                for key, value in match_create.model_dump().items():
                    setattr(existing_match, key, value)
                updated += 1
            elif not existing_match:
                new_match = Match(**match_create.model_dump())
                db.add(new_match)
                inserted += 1
        
        db.commit()
        return {
            "success": True, 
            "inserted": inserted, 
            "updated": updated,
            "total": len(fixtures)
        }

    async def sync_full_season(self, db: Session, league: int, season: int) -> dict:
        """ တစ်ရာသီစာ ပွဲစဉ်များကို sync လုပ်ခြင်း """
        result = await self.get_fixtures(league=league, season=season)
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}
            
        fixtures = result.get("response", [])
        if not fixtures:
            return {"success": False, "message": "No fixtures found"}
            
        sync_result = self._process_sync(db, fixtures)
        logger.info(f"Full Season Sync for League {league}: {sync_result}")
        return sync_result

    async def sync_daily_fixtures(self, db: Session, target_date: str) -> dict:
        """ နေ့စဉ်ပွဲစဉ်များကို sync လုပ်ခြင်း (Filter by SUPPORTED_LEAGUES) """
        result = await self.get_fixtures_by_date(target_date)
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}
            
        all_fixtures = result.get("response", [])
        
        # Filter only supported leagues
        filtered_fixtures = [
            f for f in all_fixtures 
            if f.get("league", {}).get("id") in settings.SUPPORTED_LEAGUES
        ]
        
        if not filtered_fixtures:
            return {"success": True, "message": "No matches for supported leagues today", "updated": 0}
            
        sync_result = self._process_sync(db, filtered_fixtures)
        logger.info(f"Daily Sync for {target_date}: {sync_result}")
        return sync_result
    
    async def sync_live_matches(self, db: Session) -> dict:
        """ Sync live matches from API-Football /fixtures?live=all """
        # Check if there are any live matches in database
        live_statuses = ["1H", "2H", "HT", "LIVE"]
        live_matches_count = db.query(Match).filter(Match.status.in_(live_statuses)).count()
        
        if live_matches_count == 0:
            return {"success": True, "message": "No live matches in database, skipping API call", "updated": 0}
        
        # Get live fixtures from API
        result = await self.get_live_fixtures()
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}
            
        fixtures = result.get("response", [])
        if not fixtures:
            return {"success": True, "message": "No live fixtures from API", "updated": 0}

        # Filter only supported leagues for live matches as well
        filtered_fixtures = [
            f for f in fixtures 
            if f.get("league", {}).get("id") in settings.SUPPORTED_LEAGUES
        ]
        
        if not filtered_fixtures:
            return {"success": True, "message": "No live matches for supported leagues", "updated": 0}
            
        sync_result = self._process_sync(db, filtered_fixtures)
        logger.info(f"Live Sync: {sync_result}")
        return sync_result
    
    async def get_match_events(self, fixture_id: int) -> Optional[dict]:
        """ Get match events (goals, cards, substitutions) for a specific fixture """
        endpoint = f"{self.base_url}/fixtures/events"
        params = {"fixture": fixture_id}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                return data
        except Exception as e:
            logger.error(f"Error fetching events for fixture {fixture_id}: {e}")
            return None
    
    async def get_match_lineup(self, fixture_id: int) -> Optional[dict]:
        """ Get match lineup for a specific fixture """
        endpoint = f"{self.base_url}/fixtures/lineups"
        params = {"fixture": fixture_id}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                return data
        except Exception as e:
            logger.error(f"Error fetching lineup for fixture {fixture_id}: {e}")
            return None
    
    async def get_match_h2h(self, fixture_id: int) -> Optional[dict]:
        """ Get head-to-head statistics for a specific fixture """
        endpoint = f"{self.base_url}/fixtures/headtohead"
        params = {"fixture": fixture_id}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                return data
        except Exception as e:
            logger.error(f"Error fetching h2h for fixture {fixture_id}: {e}")
            return None
    
    async def get_match_odds(self, fixture_id: int) -> Optional[dict]:
        """ Get betting odds for a specific fixture """
        endpoint = f"{self.base_url}/odds"
        params = {"fixture": fixture_id}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                return data
        except Exception as e:
            logger.error(f"Error fetching odds for fixture {fixture_id}: {e}")
            return None

football_service = FootballAPIService()