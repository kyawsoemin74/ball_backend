from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.odds import Odds


class OddsRepository:
    async def get_fixture_odds(self, db: AsyncSession, fixture_id: int) -> list[Odds]:
        result = await db.execute(select(Odds).where(Odds.fixture_id == fixture_id))
        return list(result.scalars().all())

    async def get_by_match(self, db: AsyncSession, fixture_id: int) -> list[Odds]:
        return await self.get_fixture_odds(db, fixture_id)

    async def find_by_business_identity(
        self,
        db: AsyncSession,
        fixture_id: int,
        bookmaker_name: str,
        market_name: str,
        selection: str,
    ) -> Odds | None:
        result = await db.execute(
            select(Odds).where(
                Odds.fixture_id == fixture_id,
                Odds.bookmaker_name == bookmaker_name,
                Odds.market_name == market_name,
                Odds.selection == selection,
            )
        )
        return result.scalar_one_or_none()

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

    async def snapshot_matches_rows(self, db: AsyncSession, fixture_id: int, rows: list[dict]) -> bool:
        """Verify a committed snapshot using the canonical Odds natural key."""
        expected_keys = {
            (row["bookmaker_name"], row["market_name"], row["selection"])
            for row in rows
        }
        if not expected_keys:
            return False
        result = await db.execute(select(Odds).where(Odds.fixture_id == fixture_id))
        actual_keys = {
            (row.bookmaker_name, row.market_name, row.selection)
            for row in result.scalars().all()
        }
        return actual_keys == expected_keys
