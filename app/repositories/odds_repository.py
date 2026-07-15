from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.odds import Odds


class OddsRepository:
    async def get_fixture_odds(self, db: AsyncSession, fixture_id: int) -> list[Odds]:
        result = await db.execute(select(Odds).where(Odds.fixture_id == fixture_id))
        return list(result.scalars().all())

    async def delete_fixture_odds(self, db: AsyncSession, fixture_id: int) -> None:
        await db.execute(delete(Odds).where(Odds.fixture_id == fixture_id))

    async def upsert_many(self, db: AsyncSession, rows: list[dict]) -> None:
        if not rows:
            return

        insert_stmt = pg_insert(Odds).values(rows)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            constraint="uq_odds_fixture_bookmaker_market_selection",
            set_={
                "odd_value": insert_stmt.excluded.odd_value,
                "myanmar_odd": insert_stmt.excluded.myanmar_odd,
                "last_updated": insert_stmt.excluded.last_updated,
            },
        )
        await db.execute(upsert_stmt)

    async def replace_fixture_odds(self, db: AsyncSession, fixture_id: int, rows: list[dict]) -> None:
        if not rows:
            await self.delete_fixture_odds(db, fixture_id)
            return

        await self.delete_fixture_odds(db, fixture_id)
        await self.upsert_many(db, rows)
