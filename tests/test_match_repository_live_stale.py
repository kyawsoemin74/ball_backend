import asyncio
from datetime import datetime, timedelta, timezone

from app.repositories.match_repository import MatchRepository


class FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    async def execute(self, query):
        query.compile(compile_kwargs={"literal_binds": True})
        return FakeScalarResult([])


def test_get_live_stale_builds_a_valid_query():
    repo = MatchRepository()

    async def run_case():
        return await repo.get_live_stale(
            FakeSession(),
            {999},
            datetime.now(timezone.utc) - timedelta(hours=24),
        )

    result = asyncio.run(run_case())

    assert result == []
