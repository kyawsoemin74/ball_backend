import asyncio
import json
import sys

sys.path.insert(0, "/usr/src/app")

from sqlalchemy import text
from app.db import async_session
from app.services.football import football_service


async def main():
    payload = await football_service.league_service.league_provider.get_all_leagues()
    candidates = (payload or {}).get("response") or []
    async with async_session() as db:
        rows = []
        seen = set()
        for item in candidates:
            league = item.get("league") or {}
            country = item.get("country") or {}
            provider_id = league.get("id")
            name = league.get("name")
            country_name = country.get("name")
            if provider_id is None or not name or not country_name:
                continue
            key = (str(name).strip(), str(country_name).strip())
            if key in seen:
                continue
            seen.add(key)
            existing_name = await db.execute(
                text("SELECT league_id, name FROM leagues WHERE lower(name)=lower(:name) AND lower(coalesce(country,''))=lower(:country)"),
                {"name": name, "country": country_name},
            )
            existing_identity = await db.execute(
                text("SELECT league_id FROM leagues WHERE provider='api-football' AND provider_id=:provider_id"),
                {"provider_id": str(provider_id)},
            )
            name_rows = existing_name.all()
            identity_rows = existing_identity.all()
            if not name_rows and not identity_rows:
                rows.append({
                    "name": name,
                    "country": country_name,
                    "country_code": country.get("code"),
                    "provider_id": str(provider_id),
                })
                if len(rows) >= 10:
                    break
        print(json.dumps({"provider_candidates": len(candidates), "unmapped_candidates": rows}, default=str))


asyncio.run(main())
