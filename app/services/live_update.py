import httpx
import logging
from typing import Optional
from sqlalchemy.orm import Session

from app.models.match import Match
from app.services.football import FootballAPIService

logger = logging.getLogger(__name__)

# Finished statuses that shouldn't be updated anymore
FINISHED_STATUSES = {"FT", "AET", "PEN", "P", "CANC", "ABD", "AWD", "WO"}
LIVE_STATUSES = {"1H", "2H", "HT", "ET", "LIVE", "BT", "P"}

class LiveUpdateService(FootballAPIService):
    async def get_live_fixtures(self) -> Optional[dict]:
        
        endpoint = f"{self.base_url}/fixtures"
        params = {
            "live": "all"
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                return data
        except httpx.HTTPError as e:
            logger.error(f"HTTP error occurred: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching live fixtures: {e}")
            return None

    def parse_live_fixture(self, fixture: dict) -> Optional[dict]:
        """
        Parse live fixture data to update dictionary.
        
        Args:
            fixture: Fixture dictionary from api-football.com
            
        Returns:
            Dictionary with update data or None if parsing fails
        """
        try:
            fixture_data = fixture.get("fixture", {})
            goals = fixture.get("goals", {})
            
            # Get fixture ID
            fixture_id = fixture_data.get("id")
            
            # Parse scores
            home_score = goals.get("home") or 0
            away_score = goals.get("away") or 0
            
            # Parse elapsed time
            elapsed = fixture_data.get("status", {}).get("elapsed", 0) or 0
            
            # Parse status
            fixture_status = fixture_data.get("status", {}).get("short", "NS")
            
            return {
                "fixture_id": fixture_id,
                "home_score": home_score,
                "away_score": away_score,
                "elapsed": elapsed,
                "status": fixture_status
            }
        except Exception as e:
            logger.error(f"Error parsing live fixture: {e}")
            return None

    async def update_live_matches(self, db: Session) -> dict:
        """
        Fetch live fixtures from API and update database.
        
        Args:
            db: Database session
            
        Returns:
            Dictionary with update results
        """
        # Fetch live fixtures from API
        result = await self.get_live_fixtures()
        
        if not result or "response" not in result:
            return {
                "success": False,
                "message": "No live fixtures fetched from API",
                "updated": 0
            }
        
        fixtures = result.get("response", [])
        
        if not fixtures:
            return {
                "success": True,
                "message": "No live matches at the moment",
                "updated": 0
            }
        
        updated = 0
        # Performance Optimization: Batch query for live matches
        update_data_list = []
        for fixture in fixtures:
            data = self.parse_live_fixture(fixture)
            if data:
                update_data_list.append(data)
        
        fixture_ids = [d["fixture_id"] for d in update_data_list]
        existing_matches = db.query(Match).filter(Match.fixture_id.in_(fixture_ids)).all()
        match_map = {m.fixture_id: m for m in existing_matches}
        
        for update_data in update_data_list:
            match = match_map.get(update_data["fixture_id"])
            
            # Data Integrity: ပွဲပြီးသွားတဲ့ status မဟုတ်မှသာ update လုပ်မယ်
            if match and match.status not in FINISHED_STATUSES:
                # တကယ်ပြောင်းလဲမှုရှိမှသာ database ကို write လုပ်မယ် (Optimization)
                has_changed = (
                    match.home_score != update_data["home_score"] or
                    match.away_score != update_data["away_score"] or
                    match.elapsed != update_data["elapsed"] or
                    match.status != update_data["status"]
                )
                
                if has_changed:
                    match.home_score = update_data["home_score"]
                    match.away_score = update_data["away_score"]
                    match.elapsed = update_data["elapsed"]
                    match.status = update_data["status"]
                    updated += 1
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Live update completed: {updated} matches updated",
            "updated": updated
        }


# Singleton instance
live_update_service = LiveUpdateService()