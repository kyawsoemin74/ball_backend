from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match_h2h import MatchH2H


class H2HRepository:
    async def get_by_h2h_key(self, db: AsyncSession, h2h_key: str) -> MatchH2H | None:
        result = await db.execute(select(MatchH2H).where(MatchH2H.h2h_key == h2h_key))
        return result.scalar_one_or_none()

    async def delete_by_h2h_key(self, db: AsyncSession, h2h_key: str) -> None:
        await db.execute(delete(MatchH2H).where(MatchH2H.h2h_key == h2h_key))

    async def upsert_one(self, db: AsyncSession, h2h_key: str, data: list[dict]) -> MatchH2H:
        insert_stmt = pg_insert(MatchH2H).values({"h2h_key": h2h_key, "data": data})
        upsert_stmt = insert_stmt.on_conflict_do_update(
            constraint="match_h2h_h2h_key_key",
            set_={"data": insert_stmt.excluded.data},
        )
        await db.execute(upsert_stmt)

        record = await self.get_by_h2h_key(db, h2h_key)
        if record is None:
            raise RuntimeError(f"H2H upsert failed for h2h_key={h2h_key}")
        return record
