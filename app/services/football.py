import asyncio
import httpx
import logging
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.models.match import Match
from app.models.league import League
from app.models.team import Team
from app.models.standings import Standings
from app.models.odds import Odds
from app.schemas.match import MatchCreate
from app.schemas.league import LeagueCreate
from app.schemas.team import TeamCreate
from app.schemas.standings import StandingCreate
from app.core.config import settings

# Constants for match statuses
FINISHED_STATUSES = {"FT", "AET", "PEN", "P", "CANC", "ABD", "AWD", "WO"}
LIVE_STATUSES = {"1H", "2H", "HT", "ET", "LIVE", "BT", "P"}

logger = logging.getLogger(__name__)

class FootballAPIService:
    def __init__(self):
        self.base_url = settings.FOOTBALL_API_BASE_URL
        self.api_key = settings.FOOTBALL_API_KEY
        self.headers = {
            "x-apisports-key": self.api_key,
            "Content-Type": "application/json"
        }

    def _select_main_line_by_id(self, market_id: int, market_values: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Select the main line for the exact 1xBet market IDs.
        """
        if market_id == 1:
            outcomes = {item["selection"].strip().lower(): item for item in market_values}
            required = {"home", "draw", "away"}
            if required.issubset(outcomes):
                return [outcomes["home"], outcomes["draw"], outcomes["away"]]
            return []

        if market_id in {5, 45}:
            lines: Dict[str, Dict[str, Dict[str, Any]]] = {}
            for item in market_values:
                parts = str(item["selection"]).strip().lower().split()
                if len(parts) < 2:
                    continue
                side, line = parts[0], " ".join(parts[1:])
                if side not in {"over", "under"}:
                    continue
                lines.setdefault(line, {})[side] = item

            valid_lines = [group for group in lines.values() if "over" in group and "under" in group]
            if not valid_lines:
                return []

            def line_score(group: Dict[str, Dict[str, Any]]) -> float:
                return (abs(group["over"]["odd_float"] - 2.0) + abs(group["under"]["odd_float"] - 2.0)) / 2.0

            selected = min(valid_lines, key=line_score)
            return [selected["over"], selected["under"]]

        if market_id == 4:
            pairs: Dict[float, Dict[str, Dict[str, Any]]] = {}
            for item in market_values:
                selection = str(item["selection"]).strip()
                lower = selection.lower()
                if lower.startswith("home "):
                    side = "home"
                    handicap_text = selection[5:].strip()
                elif lower.startswith("away "):
                    side = "away"
                    handicap_text = selection[5:].strip()
                else:
                    continue

                try:
                    handicap = float(handicap_text)
                except ValueError:
                    continue

                spread = abs(handicap)
                pairs.setdefault(spread, {})[side] = item

            valid_pairs = [group for group in pairs.values() if "home" in group and "away" in group]
            if not valid_pairs:
                return []

            def handicap_score(group: Dict[str, Dict[str, Any]]) -> float:
                return (abs(group["home"]["odd_float"] - 2.0) + abs(group["away"]["odd_float"] - 2.0)) / 2.0

            selected = min(valid_pairs, key=handicap_score)
            return [selected["home"], selected["away"]]

        return []

    def _is_target_market(self, market_id: int) -> bool:
        """Return True only for the exact 1xBet market IDs we support."""
        return market_id in {1, 4, 5, 45}

    def _get_1xbet_bookmaker(self, bookmakers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Return the 1xBet bookmaker entry or None if missing."""
        return next((bookmaker for bookmaker in bookmakers if bookmaker.get("id") == 11), None)

    def _filter_main_lines(self, bookmaker_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Filter odds data for the exact 1xBet market IDs: 1, 4, 5, 45.
        Uses strict pairing and discards incomplete market pairs.
        """
        filtered_odds = []

        for bet in bookmaker_data.get("bets", []):
            market_id = bet.get("id")
            market_name = bet.get("name", "")

            if not self._is_target_market(market_id):
                continue

            market_values = []
            for value in bet.get("values", []):
                selection = value.get("value")
                odd_value = value.get("odd")
                if selection is None or odd_value is None:
                    continue
                try:
                    odd_float = float(odd_value)
                except (ValueError, TypeError):
                    continue

                market_values.append({
                    "selection": str(selection).strip(),
                    "odd": str(odd_value),
                    "odd_float": odd_float
                })

            main_lines = self._select_main_line_by_id(market_id, market_values)
            for line in main_lines:
                filtered_odds.append({
                    "fixture_id": None,
                    "bookmaker_name": bookmaker_data.get("name", "1xBet"),
                    "market_name": market_name,
                    "selection": line["selection"],
                    "odd_value": line["odd"]
                })

        return filtered_odds

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
                match_id=f_info.get("id"),
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
        
        match_ids = [m.match_id for m in parsed_matches]
        existing_matches_list = db.query(Match).filter(Match.match_id.in_(match_ids)).all()
        match_map = {m.match_id: m for m in existing_matches_list}

        inserted = 0
        updated = 0
        
        for match_create in parsed_matches:
            existing_match = match_map.get(match_create.match_id)
            
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
        result = await self.get_live_fixtures()
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}
            
        fixtures = result.get("response", [])
        if not fixtures:
            return {"success": True, "message": "No live fixtures from API", "updated": 0}

        filtered_fixtures = [
            f for f in fixtures 
            if f.get("league", {}).get("id") in settings.SUPPORTED_LEAGUES
        ]
        
        if not filtered_fixtures:
            return {"success": True, "message": "No live matches for supported leagues", "updated": 0}
            
        sync_result = self._process_sync(db, filtered_fixtures)
        logger.info(f"Live Sync: {sync_result}")
        return sync_result
    
    async def get_match_events(self, match_id: int) -> Optional[dict]:
        """ Get match events (goals, cards, substitutions) for a specific fixture """
        endpoint = f"{self.base_url}/fixtures/events"
        params = {"fixture": match_id}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                return data
        except Exception as e:
            logger.error(f"Error fetching events for fixture {match_id}: {e}")
            return None
    
    async def get_match_lineup(self, match_id: int) -> Optional[dict]:
        """ Get match lineup for a specific fixture """
        endpoint = f"{self.base_url}/fixtures/lineups"
        params = {"fixture": match_id}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                return data
        except Exception as e:
            logger.error(f"Error fetching lineup for fixture {match_id}: {e}")
            return None
    
    async def get_match_h2h(self, match_id: int) -> Optional[dict]:
        """ Get head-to-head statistics for a specific fixture """
        endpoint = f"{self.base_url}/fixtures/headtohead"
        params = {"fixture": match_id}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                return data
        except Exception as e:
            logger.error(f"Error fetching h2h for fixture {match_id}: {e}")
            return None
    
    async def get_match_odds(self, match_id: int) -> Optional[dict]:
        """ Get betting odds for a specific fixture """
        endpoint = f"{self.base_url}/odds"
        params = {"fixture": match_id}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                return data
        except Exception as e:
            logger.error(f"Error fetching odds for fixture {match_id}: {e}")
            return None

    async def get_cached_odds(self, db: Session, fixture_id: int) -> dict:
        """Get odds with smart caching: return DB if <30min old, else fetch if match not started.
        
        Uses Smart Main-Line Filter to ensure only main lines are stored and returned.
        Includes debug logging when fetching from API.
        """
        from datetime import timedelta

        # Check if match has started (status not NS or similar pre-match status)
        match = db.query(Match).filter(Match.match_id == fixture_id).first()
        if not match:
            return {"error": "Match not found"}

        match_started = match.status not in ["NS", "TBD", "PST"]  # Assuming these are pre-match statuses

        # Check DB for existing odds
        existing_odds = db.query(Odds).filter(Odds.fixture_id == fixture_id).all()
        if existing_odds:
            latest_update = max(o.last_updated for o in existing_odds) if existing_odds else None
            if latest_update and (datetime.now(timezone.utc) - latest_update) < timedelta(minutes=30) or match_started:
                # Return DB data from any bookmaker saved for this fixture.
                odds_data = [{
                    "bookmaker": o.bookmaker_name,
                    "market": o.market_name,
                    "selection": o.selection,
                    "odd": o.odd_value,
                    "updated_at": o.last_updated.isoformat() if o.last_updated else None
                } for o in existing_odds]
                return {"source": "database", "odds": odds_data, "cached": True, "match_started": match_started}

        # If match started, don't fetch API, return DB even if old
        if match_started:
            odds_data = [{
                "bookmaker": o.bookmaker_name,
                "market": o.market_name,
                "selection": o.selection,
                "odd": o.odd_value,
                "updated_at": o.last_updated.isoformat() if o.last_updated else None
            } for o in existing_odds]
            return {"source": "database", "odds": odds_data, "cached": True, "match_started": True, "reason": "match_started"}

        # Fetch from API and update DB
        result = await self.get_match_odds(fixture_id)
        if not result or "response" not in result:
            return {"error": "API error"}

        responses = result.get("response", [])
        if not responses:
            return {"odds": [], "source": "api", "cached": False, "match_started": match_started, "reason": "no_data"}

        odds_to_upsert = []
        one_xbet_missing = True

        for item in responses:
            if item.get("fixture", {}).get("id") != fixture_id:
                continue

            bookmaker = self._get_1xbet_bookmaker(item.get("bookmakers", []))
            if not bookmaker:
                continue

            one_xbet_missing = False
            filtered_odds = self._filter_main_lines(bookmaker)

            for record in filtered_odds:
                record["fixture_id"] = fixture_id
                odds_to_upsert.append(record)

        if odds_to_upsert:
            db.query(Odds).filter(Odds.fixture_id == fixture_id).delete(synchronize_session=False)
            for record in odds_to_upsert:
                db.add(Odds(**record))
            db.commit()
        else:
            db.commit()

        if not odds_to_upsert:
            reason = "1xbet_data_not_found" if one_xbet_missing else "filtered_no_odds"
            return {"odds": [], "source": "api", "cached": False, "match_started": match_started, "reason": reason}

        odds_data = [{
            "bookmaker": r["bookmaker_name"],
            "market": r["market_name"],
            "selection": r["selection"],
            "odd": r["odd_value"],
            "updated_at": datetime.now(timezone.utc).isoformat()
        } for r in odds_to_upsert]

        return {
            "source": "api",
            "odds": odds_data,
            "cached": False,
            "match_started": match_started,
            "updated": len(odds_to_upsert)
        }

    async def get_league_details(self, league_id: int) -> Optional[dict]:
        """ Get league details """
        endpoint = f"{self.base_url}/leagues"
        params = {"id": league_id}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                return data
        except Exception as e:
            logger.error(f"Error fetching league {league_id}: {e}")
            return None

    async def get_team_details(self, team_id: int) -> Optional[dict]:
        """ Get team details """
        endpoint = f"{self.base_url}/teams"
        params = {"id": team_id}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                return data
        except Exception as e:
            logger.error(f"Error fetching team {team_id}: {e}")
            return None

    async def get_league_standings(self, league_id: int, season: int) -> Optional[dict]:
        """ Get league standings """
        endpoint = f"{self.base_url}/standings"
        params = {"league": league_id, "season": season}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                return data
        except Exception as e:
            logger.error(f"Error fetching standings for league {league_id} season {season}: {e}")
            return None

    def upsert_league(self, db: Session, league_data: dict) -> League:
        """ Upsert league """
        league_id = league_data["league"]["id"]
        existing = db.query(League).filter(League.league_id == league_id).first()
        if existing:
            for key, value in league_data["league"].items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
            db.commit()
            return existing
        else:
            new_league = League(
                league_id=league_id,
                name=league_data["league"]["name"],
                country=league_data["league"]["country"],
                logo=league_data["league"]["logo"],
                season=league_data["seasons"][0]["year"] if league_data.get("seasons") else None
            )
            db.add(new_league)
            db.commit()
            db.refresh(new_league)
            return new_league

    def upsert_team(self, db: Session, team_data: dict) -> Team:
        """ Upsert team """
        team_id = team_data["team"]["id"]
        existing = db.query(Team).filter(Team.team_id == team_id).first()
        if existing:
            for key, value in team_data["team"].items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
            db.commit()
            return existing
        else:
            new_team = Team(
                team_id=team_id,
                name=team_data["team"]["name"],
                country=team_data["team"]["country"],
                logo=team_data["team"]["logo"],
                stadium=team_data["venue"]["name"] if team_data.get("venue") else None,
                founded=team_data["team"]["founded"]
            )
            db.add(new_team)
            db.commit()
            db.refresh(new_team)
            return new_team

    def upsert_standings(self, db: Session, standings_data: dict, league_id: int, season: str):
        """ Upsert standings """
        # Clear existing standings for this league and season
        db.query(Standings).filter(
            Standings.league_id == league_id,
            Standings.season == season
        ).delete()
        db.commit()

        for standing in standings_data:
            new_standing = Standings(
                league_id=league_id,
                season=season,
                team_id=standing["team"]["id"],
                position=standing["rank"],
                points=standing["points"],
                played=standing["all"]["played"],
                won=standing["all"]["win"],
                drawn=standing["all"]["draw"],
                lost=standing["all"]["lose"],
                goals_for=standing["all"]["goals"]["for"],
                goals_against=standing["all"]["goals"]["against"],
                goal_difference=standing["goalsDiff"]
            )
            db.add(new_standing)
        db.commit()

football_service = FootballAPIService()