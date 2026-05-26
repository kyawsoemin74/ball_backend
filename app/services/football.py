import json
import asyncio
import httpx
import logging
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
import re

from app.models.match import Match
from app.models.league import League
from app.models.team import Team
from app.models.standing import Standings
from app.models.odds import Odds
from app.models.match_h2h import MatchH2H
from app.models.match_lineup import MatchLineup
from app.models.match_event import MatchEvent
from app.schemas.match import MatchCreate
from app.schemas.league import LeagueCreate
from app.schemas.team import TeamCreate
from app.schemas.standing import StandingCreate
from app.cache import cache_get_json, cache_set_json, cache_delete, cache_delete_sync, make_cache_key
from app.core.config import settings

# Constants for match statuses
FINISHED_STATUSES = {"FT", "AET", "PEN", "CANC", "ABD", "AWD", "WO"}
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

    def _extract_id_from_logo(self, obj: dict) -> Optional[int]:
        """Helper to extract team ID from logo URL using regex."""
        url = obj.get("logo")
        if not url or not isinstance(url, str):
            return None
        m = re.search(r"/teams/(\d+)", url)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, TypeError):
                return None
        return None

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

            date_str = f_info.get("date")
            if not date_str:
                return None
                
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))

            # Extract team IDs, prioritizing the 'id' field, then logo regex
            home_team_id = f_teams.get("home", {}).get("id") or self._extract_id_from_logo(f_teams.get("home", {}))
            away_team_id = f_teams.get("away", {}).get("id") or self._extract_id_from_logo(f_teams.get("away", {}))
            return MatchCreate(
                match_id=int(f_info.get("id")),
                league_id=f_league.get("id"),
                league_name=f_league.get("name"),
                league_logo=f_league.get("logo"),
                country_name=f_league.get("country"),
                country_logo=f_league.get("flag"),
                match_time=dt,
                status=f_info.get("status", {}).get("short", "NS"),
                elapsed=f_info.get("status", {}).get("elapsed", 0) or 0, # Ensure elapsed is not None
                home_team=f_teams.get("home", {}).get("name"),
                home_team_id=home_team_id,
                home_team_logo=f_teams.get("home", {}).get("logo"),
                away_team=f_teams.get("away", {}).get("name"),
                away_team_id=away_team_id,
                away_team_logo=f_teams.get("away", {}).get("logo"),
                home_score=f_goals.get("home") or 0,
                away_score=f_goals.get("away") or 0,
                venue_name=f_info.get("venue", {}).get("name", "Unknown"),
                venue_city=f_info.get("venue", {}).get("city", "Unknown")
            )
        except Exception as e:
            logger.error(f"Parsing error for Fixture ID {fixture.get('fixture', {}).get('id')}: {e}")
            return None

    async def _process_sync(self, db: AsyncSession, fixtures: list) -> dict:
        """
        Simplified upsert logic for matches only. 
        Removed team dependency checking and side effects (notifications/sockets).
        """
        parsed_matches = []
        for fixture_raw in fixtures:
            m = self.parse_fixture_to_match(fixture_raw)
            if m:
                parsed_matches.append(m)
            else:
                logger.warning(f"Fixture ID {fixture_raw.get('fixture', {}).get('id')} failed parsing.")
        
        if not parsed_matches:
            return {"success": True, "inserted": 0, "updated": 0, "total": 0}

        match_ids = [m.match_id for m in parsed_matches]
        result = await db.execute(select(Match).where(Match.match_id.in_(match_ids)))
        match_map = {m.match_id: m for m in result.scalars().all()}

        inserted = 0
        updated = 0

        for match_create in parsed_matches:
            existing_match = match_map.get(match_create.match_id)
            db_data = match_create.model_dump()

            if existing_match:
                for key, value in db_data.items():
                    setattr(existing_match, key, value)
                updated += 1
            else:
                db.add(Match(**db_data))
                inserted += 1
        
        await db.commit()
        await cache_delete(make_cache_key("live_matches"))

        return {
            "success": True,
            "inserted": inserted,
            "updated": updated,
            "total": len(fixtures),
        }


    async def sync_full_season(self, db: AsyncSession, league: int, season: int) -> dict:
        """ တစ်ရာသီစာ ပွဲစဉ်များကို sync လုပ်ခြင်း """
        result = await self.get_fixtures(league=league, season=season)
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}
            
        fixtures = result.get("response", [])
        if not fixtures:
            return {"success": False, "message": "No fixtures found"}
            
        sync_result = await self._process_sync(db, fixtures)
        logger.info(f"Full Season Sync for League {league}: {sync_result}")
        return sync_result

    async def sync_daily_fixtures(self, db: AsyncSession, target_date: str) -> dict:
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
            
        sync_result = await self._process_sync(db, filtered_fixtures)
        logger.info(f"Daily Sync for {target_date}: {sync_result}")
        return sync_result
    
    async def sync_live_matches(self, db: AsyncSession) -> dict:
        """ Sync live matches from API-Football /fixtures?live=all """
        # 1. Fetch currently live matches from API
        result = await self.get_live_fixtures()
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}
            
        fixtures = result.get("response", [])

        # Do not return early if fixtures is empty. 
        # We need to check if matches in our DB are still live but missing from API's live feed.

        # 2. Identify "stale" live matches in DB.
        # This handles cases where a match finishes and is removed from /fixtures?live=all
        # before our last sync cycle could update it to 'FT'.
        api_live_ids = {f["fixture"]["id"] for f in fixtures if f.get("fixture") and f["fixture"].get("id")}
        
        # Look for matches in DB that are marked as LIVE but missing from API's live feed.
        # We check matches scheduled to start within the last 24 hours to handle missed updates.
        stale_threshold = datetime.now(timezone.utc) - timedelta(hours=24)
        
        stale_query = select(Match).where(
            Match.status.notin_(FINISHED_STATUSES),
            Match.match_time >= stale_threshold
        )
        if api_live_ids:
            stale_query = stale_query.where(Match.match_id.not_in(list(api_live_ids)))
            
        res = await db.execute(stale_query)
        stale_matches = res.scalars().all()
        
        if stale_matches:
            stale_ids = [str(m.match_id) for m in stale_matches]
            logger.info("Syncing %d stale matches that are no longer in live feed", len(stale_ids))
            
            # Fetch latest data for these specific IDs using the ids parameter (hyphen separated)
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    stale_resp = await client.get(f"{self.base_url}/fixtures", headers=self.headers, params={"ids": "-".join(stale_ids)})
                    if stale_resp.status_code == 200:
                        stale_data = stale_resp.json()
                        if stale_data.get("response"):
                            fixtures.extend(stale_data["response"])
            except Exception as e:
                logger.error(f"Failed to fetch updates for stale matches: {e}")

        filtered_fixtures = [
            f for f in fixtures 
            if f.get("league", {}).get("id") in settings.SUPPORTED_LEAGUES
        ]
        
        if not filtered_fixtures:
            return {"success": True, "message": "No live matches for supported leagues", "updated": 0}
            
        sync_result = await self._process_sync(db, filtered_fixtures)
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
        """ Get match lineup for a specific fixture from API """
        endpoint = f"{self.base_url}/fixtures/lineups"
        params = {"fixture": match_id}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Error fetching lineup for fixture {match_id}: {e}")
            return None

    async def get_cached_match_lineup(self, db: AsyncSession, match_id: int) -> Optional[List[Dict[str, Any]]]:
        """ Get match lineup with caching and DB persistence """
        cache_key = make_cache_key("match", match_id, "lineup")
        
        # 1. Check Redis
        cached = await cache_get_json(cache_key)
        if cached is not None:
            return cached

        # 2. Check Database
        res = await db.execute(select(MatchLineup).where(MatchLineup.match_id == match_id))
        db_record = res.scalar_one_or_none()
        if db_record:
            await cache_set_json(cache_key, db_record.data, 3600)
            return db_record.data

        # 3. Fetch from API if not in Cache/DB
        api_res = await self.get_match_lineup(match_id)
        if not api_res or "response" not in api_res:
            return None
        
        lineup_data = api_res["response"]
        if lineup_data:
            # Save to DB
            new_lineup = MatchLineup(match_id=match_id, data=lineup_data)
            db.add(new_lineup)
            await db.commit()
            # Save to Cache
            await cache_set_json(cache_key, lineup_data, 3600)
            
        return lineup_data
    
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

    async def get_cached_h2h(self, db: AsyncSession, team1_id: int, team2_id: int, match_id: int) -> Optional[dict]:
        """
        Symmetric H2H caching strategy with time-based window logic.
        """
        # 1. Symmetric Key Generation
        ids = sorted([team1_id, team2_id])
        h2h_key = f"{ids[0]}-{ids[1]}"
        redis_key = f"match:h2h:{h2h_key}"

        # 2. Match Details for timing
        res = await db.execute(select(Match).where(Match.match_id == match_id))
        match = res.scalar_one_or_none()
        if not match:
            return None

        now = datetime.now(timezone.utc)
        match_time = match.match_time if match.match_time.tzinfo else match.match_time.replace(tzinfo=timezone.utc)
        time_to_kickoff = match_time - now
        
        # Determine Window
        is_window_1 = time_to_kickoff > timedelta(hours=24)

        # 3. Apply Strategic Logic
        
        # WINDOW 2: Check Redis Cache (if within 24h or live/finished)
        if not is_window_1:
            cached = await cache_get_json(redis_key)
            if cached is not None:
                return cached

        # Check Database (Both windows use DB on cache miss/bypass)
        res = await db.execute(select(MatchH2H).where(MatchH2H.h2h_key == h2h_key))
        db_record = res.scalar_one_or_none()

        if db_record:
            if not is_window_1:
                await cache_set_json(redis_key, db_record.data, 86400) # 24h TTL
            return db_record.data

        # Cache Miss / DB Miss: Fetch from API
        endpoint = f"{self.base_url}/fixtures/headtohead"
        params = {"h2h": h2h_key}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                api_data = response.json()
                
                if not api_data or "response" not in api_data:
                    return None
                
                h2h_data = api_data["response"]

                # Upsert to DB
                res = await db.execute(select(MatchH2H).where(MatchH2H.h2h_key == h2h_key))
                existing = res.scalar_one_or_none()
                
                if existing:
                    existing.data = h2h_data
                else:
                    new_h2h = MatchH2H(h2h_key=h2h_key, data=h2h_data)
                    db.add(new_h2h)
                
                await db.commit()

                # Cache if in Window 2
                if not is_window_1:
                    await cache_set_json(redis_key, h2h_data, 86400)
                
                return h2h_data

        except Exception as e:
            logger.error(f"Error fetching symmetric H2H for {h2h_key}: {e}")
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

    async def sync_match_events(self, db: AsyncSession, match_id: int) -> dict:
        """
        Fetch finalized events from API and upsert into DB.
        """
        result = await self.get_match_events(match_id)
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}
        
        events_data = result["response"]
        
        # Delete existing records for this match to ensure clean state
        await db.execute(delete(MatchEvent).where(MatchEvent.match_id == match_id))
        
        for e in events_data:
            new_event = MatchEvent(
                match_id=match_id,
                time_elapsed=e.get("time", {}).get("elapsed"),
                time_extra=e.get("time", {}).get("extra"),
                team_id=e.get("team", {}).get("id"),
                player_id=e.get("player", {}).get("id"),
                player_name=e.get("player", {}).get("name"),
                type=e.get("type"),
                detail=e.get("detail"),
                comments=e.get("comments")
            )
            db.add(new_event)
        
        await db.commit()
        await cache_delete(make_cache_key("match", match_id, "events"))
        return {"success": True, "count": len(events_data)}

    async def get_cached_match_events(self, db: AsyncSession, match_id: int) -> List[Dict[str, Any]]:
        """
        Get match events with a hybrid caching/DB strategy.
        """
        cache_key = make_cache_key("match", match_id, "events")
        cached = await cache_get_json(cache_key)
        if cached is not None:
            return cached

        # Check match status to determine source strategy
        res = await db.execute(select(Match).where(Match.match_id == match_id))
        match = res.scalar_one_or_none()
        if not match:
            return []

        # Strategy 1: If finished, prioritize DB
        if match.status in FINISHED_STATUSES:
            res = await db.execute(
                select(MatchEvent)
                .where(MatchEvent.match_id == match_id)
                .order_by(MatchEvent.time_elapsed, MatchEvent.time_extra)
            )
            db_events = res.scalars().all()
            if db_events:
                payload = [
                    {
                        "id": e.id,
                        "match_id": e.match_id,
                        "time_elapsed": e.time_elapsed,
                        "time_extra": e.time_extra,
                        "team_id": e.team_id,
                        "player_id": e.player_id,
                        "player_name": e.player_name,
                        "type": e.type,
                        "detail": e.detail,
                        "comments": e.comments
                    } for e in db_events
                ]
                await cache_set_json(cache_key, payload, settings.REDIS_TTL_STANDINGS)
                return payload

        # Strategy 2: Fetch fresh data if live or finished but missing from DB
        result = await self.get_match_events(match_id)
        if not result or "response" not in result:
            return []

        api_events = result["response"]
        payload = []
        for e in api_events:
            payload.append({
                "match_id": match_id,
                "time_elapsed": e.get("time", {}).get("elapsed"),
                "time_extra": e.get("time", {}).get("extra"),
                "team_id": e.get("team", {}).get("id"),
                "player_id": e.get("player", {}).get("id"),
                "player_name": e.get("player", {}).get("name"),
                "type": e.get("type"),
                "detail": e.get("detail"),
                "comments": e.get("comments")
            })

        if match.status in LIVE_STATUSES:
            await cache_set_json(cache_key, payload, 120)  # Short TTL for live
        elif match.status in FINISHED_STATUSES:
            # Trigger sync background if finished but missing from DB
            asyncio.create_task(self.sync_match_events(db, match_id))
            await cache_set_json(cache_key, payload, settings.REDIS_TTL_STANDINGS)

        return payload

    async def get_cached_odds(self, db: AsyncSession, fixture_id: int) -> dict:
        """Get odds with smart caching: return DB if <30min old, else fetch if match not started.
        
        Uses Smart Main-Line Filter to ensure only main lines are stored and returned.
        Caches the final filtered results for 2 minutes (120s TTL).
        """
        # Strategy: Check Redis Cache first using the new key format
        cache_key = make_cache_key("match", fixture_id, "odds")
        cached = await cache_get_json(cache_key)
        if cached is not None:
            return cached

        # Check if match has started (status not NS or similar pre-match status)
        result = await db.execute(select(Match).where(Match.match_id == fixture_id))
        match = result.scalar_one_or_none()
        if not match:
            return {"error": "Match not found"}

        match_started = match.status not in ["NS", "TBD", "PST"]  # Assuming these are pre-match statuses

        # Check DB for existing odds
        result = await db.execute(select(Odds).where(Odds.fixture_id == fixture_id))
        existing_odds = result.scalars().all()

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
                result = {"source": "database", "odds": odds_data, "cached": True, "match_started": match_started}
                await cache_set_json(cache_key, result, 120)  # Short TTL 2 mins
                return result

        # If match started, don't fetch API, return DB even if old
        if match_started:
            odds_data = [{
                "bookmaker": o.bookmaker_name,
                "market": o.market_name,
                "selection": o.selection,
                "odd": o.odd_value,
                "updated_at": o.last_updated.isoformat() if o.last_updated else None
            } for o in existing_odds]
            result = {"source": "database", "odds": odds_data, "cached": True, "match_started": True, "reason": "match_started"}
            await cache_set_json(cache_key, result, 120)  # Short TTL 2 mins
            return result

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
            await db.execute(delete(Odds).where(Odds.fixture_id == fixture_id))
            for record in odds_to_upsert:
                db.add(Odds(**record))
            await db.commit()
        else:
            await db.commit()

        if not odds_to_upsert:
            reason = "1xbet_data_not_found" if one_xbet_missing else "filtered_no_odds"
            result = {"odds": [], "source": "api", "cached": False, "match_started": match_started, "reason": reason}
            await cache_set_json(cache_key, result, 120)  # Short TTL 2 mins
            return result

        odds_data = [{
            "bookmaker": r["bookmaker_name"],
            "market": r["market_name"],
            "selection": r["selection"],
            "odd": r["odd_value"],
            "updated_at": datetime.now(timezone.utc).isoformat()
        } for r in odds_to_upsert]

        result = {
            "source": "api",
            "odds": odds_data,
            "cached": False,
            "match_started": match_started,
            "updated": len(odds_to_upsert)
        }
        await cache_set_json(cache_key, result, 120)  # Short TTL 2 mins
        return result

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

    async def upsert_league(self, db: AsyncSession, league_data: dict) -> League:
        """ Upsert league """
        league_id = league_data["league"]["id"]
        result = await db.execute(select(League).where(League.league_id == league_id))
        existing = result.scalar_one_or_none()
        if existing:
            for key, value in league_data["league"].items():
                # Only update attributes that exist on the model and are not primary keys
                if hasattr(existing, key) and key != "league_id":
                    setattr(existing, key, value)
            await db.commit()
            cache_delete_sync(make_cache_key("league", league_id))
            return existing
        else:
            new_league = League(
                # Ensure league_id is explicitly set for new records
                league_id=league_id, 
                name=league_data["league"]["name"],
                country=league_data["league"]["country"],
                logo=league_data["league"]["logo"],
                season=league_data["seasons"][0]["year"] if league_data.get("seasons") else None
            )
            db.add(new_league)
            await db.commit()
            await db.refresh(new_league)
            return new_league

    async def upsert_team(self, db: AsyncSession, team_data: dict) -> Team:
        """ Upsert team """
        team_id = team_data["team"]["id"]
        result = await db.execute(select(Team).where(Team.team_id == team_id))
        existing = result.scalar_one_or_none()
        if existing:
            # Update existing team data if necessary
            existing.name = team_data["team"]["name"]
            existing.country = team_data["team"]["country"]
            existing.logo = team_data["team"]["logo"]
            existing.stadium = team_data["venue"]["name"] if team_data.get("venue") else None
            existing.founded = team_data["team"]["founded"]
            await db.commit()
            cache_delete_sync(make_cache_key("team", team_id))
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
            await db.commit()
            await db.refresh(new_team)
            return new_team

    async def sync_standings(self, db: AsyncSession, league_id: int, season: int) -> dict:
        """ Fetch standings from API and sync to DB """
        result = await self.get_league_standings(league_id, season)
        if not result or "response" not in result or not result["response"]:
            return {"success": False, "message": "No standings data found from API"}
            
        # API-Sports nested structure: response[0] -> league -> standings[0] -> list of team objects
        try:
            standings_list = result["response"][0]["league"]["standings"][0]
            await self.upsert_standings(db, standings_list, league_id, str(season))
            return {"success": True, "league_id": league_id, "season": season, "updated": len(standings_list)}
        except (KeyError, IndexError) as e:
            logger.error(f"Error parsing standings response: {e}")
            return {"success": False, "message": "Unexpected API response format"}

    async def upsert_standings(self, db: AsyncSession, standings_data: list, league_id: int, season: str):
        """ Upsert standings """
        await db.execute(delete(Standings).where(
            Standings.league_id == league_id,
            Standings.season == season
        ))
        await db.commit()

        for standing in standings_data:
            new_standing = Standings(
                league_id=league_id,
                season=int(season),
                team_id=standing["team"]["id"],
                team_name=standing["team"]["name"],
                team_logo=standing["team"]["logo"],
                rank=standing["rank"],
                points=standing["points"],
                played=standing["all"]["played"],
                win=standing["all"]["win"],
                draw=standing["all"]["draw"],
                lose=standing["all"]["lose"],
                goals_diff=standing["goalsDiff"]
            )
            db.add(new_standing)
        await db.commit()
        cache_delete_sync(make_cache_key("standings", league_id, season))

football_service = FootballAPIService()