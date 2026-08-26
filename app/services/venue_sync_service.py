"""
Venue Sync Service - Write orchestration and normalization.

Responsibilities:
  - Normalize provider venue payloads
  - Orchestrate write operations
  - Call Repository for persistence
  - Never directly call API

Architecture:
  Provider (HTTP) → Sync Service (normalization) → Repository (SQL)
"""

import logging
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.venue_provider import VenueProvider
from app.repositories.venue_repository import VenueRepository
from app.services.base.football_client import FootballAPIClient

logger = logging.getLogger(__name__)


class VenueSyncService:
    """Sync orchestration for Venue Master."""

    def __init__(self) -> None:
        self.client = FootballAPIClient()
        self.provider = VenueProvider(self.client)
        self.repository = VenueRepository()

    @staticmethod
    def _normalize_provider_id(value: object) -> str | None:
        if isinstance(value, bool) or value is None:
            return None
        provider_id = str(value).strip()
        if not provider_id or not provider_id.isdigit() or int(provider_id) <= 0:
            return None
        return provider_id

    def normalize_fixture_venue(self, venue_payload: dict) -> dict | None:
        """
        Normalize venue from fixture endpoint.
        
        Fixture venue structure:
          {
            "id": <int>,
            "name": <str>,
            "city": <str>
          }
        
        Returns normalized dict or None if invalid.
        """
        if not isinstance(venue_payload, dict):
            return None
        
        provider_id = VenueSyncService._normalize_provider_id(venue_payload.get("id"))
        if provider_id is None:
            return None
        
        name = venue_payload.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        
        return {
            "provider": "api-football",
            "provider_id": provider_id,
            "name": name.strip(),
            "city": venue_payload.get("city", "").strip() if isinstance(venue_payload.get("city"), str) else None,
            "country": None,
            "country_code": None,
            "capacity": None,
            "surface": None,
            "image": None,
        }

    def normalize_team_venue(self, team_payload: dict) -> dict | None:
        """
        Normalize venue from team endpoint.
        
        Team venue structure:
          {
            "id": <int>,
            "name": <str>,
            "address": <str>,
            "city": <str>,
            "capacity": <int>,
            "surface": <str>,
            "image": <str>
          }
        
        Returns normalized dict or None if invalid.
        """
        if not isinstance(team_payload, dict):
            return None
        
        provider_id = VenueSyncService._normalize_provider_id(team_payload.get("id"))
        if provider_id is None:
            return None
        
        name = team_payload.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        
        return {
            "provider": "api-football",
            "provider_id": provider_id,
            "name": name.strip(),
            "city": team_payload.get("city", "").strip() if isinstance(team_payload.get("city"), str) else None,
            "country": None,
            "country_code": None,
            "capacity": team_payload.get("capacity"),
            "surface": team_payload.get("surface", "").strip() or None,
            "image": team_payload.get("image"),
        }

    async def sync_fixture_payload(self, db: AsyncSession, venue_payload: dict | None) -> dict:
        """Resolve one fixture Venue without owning the surrounding transaction."""
        if not venue_payload:
            logger.info("VENUE_MISSING")
            return {"success": True, "venue_id": None, "reason": "missing"}

        if not venue_payload.get("id"):
            logger.info("VENUE_MISSING_ID")
            return {"success": True, "venue_id": None, "reason": "missing_id"}

        normalized = self.normalize_fixture_venue(venue_payload)
        if normalized is None:
            logger.warning("VENUE_INVALID_ID")
            return {"success": True, "venue_id": None, "reason": "invalid"}

        provider = normalized["provider"]
        provider_id = normalized["provider_id"]
        existing = await self.repository.get_by_provider_id(db, provider_id, provider)
        try:
            if existing is None:
                async with db.begin_nested():
                    venue = await self.repository.upsert_one(db, normalized)
            else:
                venue = await self.repository.upsert_one(db, normalized)
        except IntegrityError:
            logger.warning("VENUE_UNIQUE_CONFLICT_RETRY", extra={"provider": provider, "provider_id": provider_id})
            venue = await self.repository.get_by_provider_id(db, provider_id, provider)
            if venue is None:
                return {"success": False, "venue_id": None, "provider_id": provider_id, "reason": "venue_conflict"}
            venue = await self.repository.upsert_one(db, normalized)
        except Exception:
            logger.exception("VENUE_DB_FAILURE", extra={"provider": provider, "provider_id": provider_id})
            raise

        logger.info(
            "VENUE_UPDATED" if existing is not None else "VENUE_CREATED",
            extra={"provider": provider, "provider_id": provider_id, "venue_id": venue.venue_id},
        )
        return {"success": True, "venue_id": venue.venue_id, "provider_id": provider_id}

    async def ensure_venues_exist(self, db: AsyncSession, venues_data: list[dict]) -> dict:
        """
        Idempotent upsert of multiple Venues.
        
        Args:
            db: Database session
            venues_data: List of normalized venue dicts
        
        Returns:
            {
              "success": bool,
              "created": int,
              "updated": int,
              "skipped": int,
              "errors": list[str]
            }
        """
        created_count = 0
        updated_count = 0
        skipped_count = 0
        errors = []

        for venue_data in venues_data:
            try:
                # Validate required fields
                if not venue_data.get("provider_id") or not venue_data.get("name"):
                    skipped_count += 1
                    continue

                provider = venue_data.get("provider", "api-football")
                provider_id = venue_data.get("provider_id")
                existing = await self.repository.get_by_provider_id(db, provider_id, provider)
                if existing is not None:
                    result = await self.repository.upsert_one(db, venue_data)
                else:
                    try:
                        async with db.begin_nested():
                            result = await self.repository.upsert_one(db, venue_data)
                    except IntegrityError:
                        logger.warning(
                            "VENUE_UNIQUE_CONFLICT_RETRY",
                            extra={"provider": provider, "provider_id": provider_id},
                        )
                        result = await self.repository.get_by_provider_id(db, provider_id, provider)
                        if result is None:
                            raise
                
                # Count: check if this was new or existing
                # For simplicity, count all as created
                created_count += 1
                
            except Exception as e:
                logger.error("Error upserting venue %s: %s", venue_data.get("provider_id"), e)
                errors.append(f"Venue {venue_data.get('provider_id')}: {str(e)}")
        
        await db.flush()
        
        return {
            "success": len(errors) == 0,
            "created": created_count,
            "updated": updated_count,
            "skipped": skipped_count,
            "errors": errors,
        }

    async def sync_fixture_venue(self, db: AsyncSession, fixture_id: int) -> dict:
        """
        Sync venue from a single fixture.
        
        Fetches fixture endpoint and upserts venue.
        """
        try:
            venue_payload = await self.provider.get_fixture_venue(fixture_id)
            
            if not venue_payload:
                return {"success": False, "reason": "No venue in fixture payload"}
            
            normalized = self.normalize_fixture_venue(venue_payload)
            if not normalized:
                return {"success": False, "reason": "Could not normalize venue payload"}
            
            result = await self.ensure_venues_exist(db, [normalized])
            return result
            
        except Exception as e:
            logger.error("Error syncing fixture venue %s: %s", fixture_id, e)
            return {"success": False, "error": str(e)}

    async def sync_team_venue(self, db: AsyncSession, team_id: int) -> dict:
        """
        Sync venue from a single team.
        
        Fetches team endpoint and upserts venue.
        """
        try:
            team_payload = await self.provider.get_team_venue(team_id)
            
            if not team_payload:
                return {"success": False, "reason": "No venue in team payload"}
            
            normalized = self.normalize_team_venue(team_payload)
            if not normalized:
                return {"success": False, "reason": "Could not normalize team venue payload"}
            
            result = await self.ensure_venues_exist(db, [normalized])
            return result
            
        except Exception as e:
            logger.error("Error syncing team venue %s: %s", team_id, e)
            return {"success": False, "error": str(e)}
