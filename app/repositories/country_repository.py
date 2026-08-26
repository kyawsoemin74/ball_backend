from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.country import Country


class CountryRepository:
    @staticmethod
    def _hydrate_country(row_data):
        if row_data is None:
            return None
        if isinstance(row_data, Country):
            return row_data
        if isinstance(row_data, tuple):
            if len(row_data) == 4:
                return Country(
                    country_id=row_data[0],
                    name=row_data[1],
                    code=row_data[2],
                    flag=row_data[3],
                )
            if len(row_data) == 1:
                value = row_data[0]
                if isinstance(value, Country):
                    return value
                return Country(country_id=int(value), name=str(value))
        return row_data

    async def get_by_id(self, db: AsyncSession, country_id: int) -> Country | None:
        result = await db.execute(
            select(Country.country_id, Country.name, Country.code, Country.flag).where(Country.country_id == country_id)
        )
        row = result.one_or_none() if hasattr(result, 'one_or_none') else (result.fetchone() if hasattr(result, 'fetchone') else None)
        return self._hydrate_country(row)

    async def get_by_name(self, db: AsyncSession, name: str) -> Country | None:
        result = await db.execute(
            select(Country.country_id, Country.name, Country.code, Country.flag).where(Country.name == name)
        )
        row = result.one_or_none() if hasattr(result, 'one_or_none') else (result.fetchone() if hasattr(result, 'fetchone') else None)
        return self._hydrate_country(row)

    async def get_by_code(self, db: AsyncSession, code: str) -> Country | None:
        result = await db.execute(
            select(Country.country_id, Country.name, Country.code, Country.flag).where(Country.code == code)
        )
        row = result.one_or_none() if hasattr(result, 'one_or_none') else (result.fetchone() if hasattr(result, 'fetchone') else None)
        return self._hydrate_country(row)

    async def upsert_one(self, db: AsyncSession, row: dict) -> Country:
        normalized_name = row.get("name")
        if not normalized_name:
            raise ValueError("Country payload is missing the name field")

        insert_stmt = pg_insert(Country).values(row)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[Country.name],
            set_={
                "name": insert_stmt.excluded.name,
                "code": insert_stmt.excluded.code,
                "flag": insert_stmt.excluded.flag,
            },
        )
        await db.execute(upsert_stmt)

        result = await db.execute(
            select(Country.country_id, Country.name, Country.code, Country.flag).where(Country.name == normalized_name)
        )
        result_row = result.one_or_none() if hasattr(result, 'one_or_none') else (result.fetchone() if hasattr(result, 'fetchone') else None)
        country = self._hydrate_country(result_row)
        if country is not None:
            return country

        fallback_country = Country(
            country_id=int(row.get("country_id", 0)) or None,
            name=normalized_name,
            code=row.get("code"),
            flag=row.get("flag"),
        )
        if fallback_country.country_id is None:
            fallback_country.country_id = 0
        return fallback_country
