import asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session
from app.repositories.team_repository import TeamRepository
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.providers.team_provider import TeamProvider
from app.services.team_sync_service import TeamSyncService


async def main():
    client = FootballAPIClient()
    team_provider = TeamProvider(client)
    team_sync = TeamSyncService(
        cache_service=CacheService(),
        team_repository=TeamRepository(),
        team_provider=team_provider,
    )

    async with async_session() as db:
        rows = await team_sync.collect_provider_evidence_for_null_provider_teams(db)
        print("TARGET_ROWS=", len(rows))
        for row in rows:
            print(row)


if __name__ == "__main__":
    asyncio.run(main())
