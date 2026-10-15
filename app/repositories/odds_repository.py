from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.odds import Odds
from app.services.odds_value import format_canonical_odd


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

        canonical_rows = [
            {**row, "odd_value": format_canonical_odd(row["odd_value"])}
            for row in rows
        ]
        insert_stmt = pg_insert(Odds).values(canonical_rows)
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
            raise ValueError("Refusing to replace an Odds snapshot with an empty candidate")

        await self.delete_fixture_odds(db, fixture_id)
        await self.upsert_many(db, rows)

    async def snapshot_matches_rows(self, db: AsyncSession, fixture_id: int, rows: list[dict]) -> bool:
        """Compare complete persisted Odds content with the expected snapshot."""
        if not rows:
            return False
        result = await db.execute(select(Odds).where(Odds.fixture_id == fixture_id))
        actual_rows = result.scalars().all()

        def row_key(row: dict | Odds) -> tuple:
            get = row.get if isinstance(row, dict) else lambda key: getattr(row, key)
            return (
                get("fixture_id"),
                get("bookmaker_name"),
                get("market_name"),
                get("selection"),
            )

        def odd_value(value) -> str | None:
            try:
                return format_canonical_odd(value)
            except ValueError:
                return None

        def row_content(row: dict | Odds) -> tuple:
            get = row.get if isinstance(row, dict) else lambda key: getattr(row, key)
            return (
                row_key(row),
                odd_value(get("odd_value")),
                get("myanmar_odd"),
                get("last_updated"),
            )

        expected = {row_content(row) for row in rows}
        actual = {row_content(row) for row in actual_rows}
        return len(actual_rows) == len(rows) and actual == expected
