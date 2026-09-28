import asyncio
import json
import sys

sys.path.insert(0, "/usr/src/app")

from sqlalchemy import text
from app.db import async_session
from app.services.football import football_service


async def main():
    names = ["Premier League", "La Liga", "Serie A", "Bundesliga", "Ligue 1"]
    async with async_session() as db:
        for name in names:
            payload = await football_service.league_service.league_provider.get_leagues_by_name(name)
            candidates = []
            for item in (payload or {}).get("response", []):
                league = item.get("league") or {}
                country = item.get("country") or {}
                provider_id = league.get("id")
                if provider_id is None:
                    continue
                existing = await db.execute(
                    text("SELECT league_id, name FROM leagues WHERE provider = 'api-football' AND provider_id = :provider_id"),
                    {"provider_id": str(provider_id)},
                )
                rows = [dict(row) for row in existing.mappings().all()]
                candidates.append({
                    "name": league.get("name"),
                    "country": country.get("name"),
                    "country_code": country.get("code"),
                    "provider_id": str(provider_id),
                    "existing": rows,
                })
            print(json.dumps({"query": name, "candidates": candidates}, default=str))


asyncio.run(main())
