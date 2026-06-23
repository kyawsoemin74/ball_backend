import asyncio
from types import SimpleNamespace

from app.services.league_structure_resolver import LeagueCompetitionType, LeagueStructureResolver


class FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class FakeDB:
    def __init__(self, row):
        self.row = row
        self.calls = 0
        self.last_query = None

    async def execute(self, query):
        self.calls += 1
        self.last_query = query
        return FakeResult(self.row)


def _resolve(match, row):
    resolver = LeagueStructureResolver()

    async def run():
        return await resolver.resolve(match, FakeDB(row))

    return asyncio.run(run())


def test_regular_league_competition_uses_league_and_season_scoped_standings():
    match = SimpleNamespace(league_id=39, league_name="Premier League", league_obj=SimpleNamespace(season="2026"))
    resolution = _resolve(match, SimpleNamespace(id=1, group_name=None))

    assert resolution.competition_type is LeagueCompetitionType.REGULAR
    assert resolution.has_standings is True
    assert resolution.is_knockout is False
    assert resolution.has_bracket is False


def test_knockout_competition_without_standings_rows_is_classified_as_knockout():
    match = SimpleNamespace(league_id=40, league_name="FA Cup", league_obj=SimpleNamespace(season="2026"))
    resolution = _resolve(match, None)

    assert resolution.competition_type is LeagueCompetitionType.KNOCKOUT
    assert resolution.has_standings is False
    assert resolution.is_knockout is True
    assert resolution.has_bracket is False


def test_group_stage_competition_is_classified_from_grouped_standings():
    match = SimpleNamespace(league_id=41, league_name="Champions League", league_obj=SimpleNamespace(season="2026"))
    resolution = _resolve(match, SimpleNamespace(id=1, group_name="Group A"))

    assert resolution.competition_type is LeagueCompetitionType.GROUP_STAGE
    assert resolution.has_standings is True
    assert resolution.is_knockout is False
    assert resolution.has_bracket is False


def test_group_plus_knockout_competition_uses_both_signals():
    match = SimpleNamespace(league_id=42, league_name="Champions League Cup", league_obj=SimpleNamespace(season="2026"))
    resolution = _resolve(match, SimpleNamespace(id=1, group_name="Group B"))

    assert resolution.competition_type is LeagueCompetitionType.GROUP_KNOCKOUT
    assert resolution.has_standings is True
    assert resolution.is_knockout is True
    assert resolution.has_bracket is False


def test_resolver_uses_match_season_before_league_season(caplog):
    resolver = LeagueStructureResolver()
    fake_db = FakeDB(SimpleNamespace(id=1, group_name=None))
    match = SimpleNamespace(
        match_id=99,
        league_id=39,
        season=2028,
        league_name="Premier League",
        league_obj=SimpleNamespace(season="2026"),
    )

    async def run():
        return await resolver.resolve(match, fake_db)

    with caplog.at_level("INFO"):
        resolution = asyncio.run(run())

    assert resolution.has_standings is True
    assert fake_db.last_query is not None
    compiled_sql = str(fake_db.last_query.compile(compile_kwargs={"literal_binds": True}))
    assert "standings.season = '2028'" in compiled_sql
    metrics = resolver.get_metrics()
    assert metrics == {
        "resolver_calls": 1,
        "match_season_used": 1,
        "league_season_fallback_used": 0,
    }
    assert "MATCH_SEASON_USED fixture_id=99 league_id=39 match_season=2028 league_season=2026" in caplog.text


def test_resolver_falls_back_to_league_season_and_logs(caplog):
    resolver = LeagueStructureResolver()
    fake_db = FakeDB(SimpleNamespace(id=1, group_name=None))
    match = SimpleNamespace(
        match_id=100,
        league_id=39,
        season=None,
        league_name="Premier League",
        league_obj=SimpleNamespace(season="2026"),
    )

    async def run():
        return await resolver.resolve(match, fake_db)

    with caplog.at_level("INFO"):
        resolution = asyncio.run(run())

    assert resolution.has_standings is True
    compiled_sql = str(fake_db.last_query.compile(compile_kwargs={"literal_binds": True}))
    assert "standings.season = '2026'" in compiled_sql
    metrics = resolver.get_metrics()
    assert metrics == {
        "resolver_calls": 1,
        "match_season_used": 0,
        "league_season_fallback_used": 1,
    }
    assert "LEAGUE_SEASON_FALLBACK_USED fixture_id=100 league_id=39 match_season=None league_season=2026" in caplog.text


def test_resolver_metrics_track_no_season_source_case():
    resolver = LeagueStructureResolver()
    fake_db = FakeDB(None)
    match = SimpleNamespace(
        match_id=101,
        league_id=39,
        season=None,
        league_name="Premier League",
        league_obj=None,
    )

    async def run():
        return await resolver.resolve(match, fake_db)

    resolution = asyncio.run(run())

    assert resolution.has_standings is False
    assert fake_db.calls == 0
    metrics = resolver.get_metrics()
    assert metrics == {
        "resolver_calls": 1,
        "match_season_used": 0,
        "league_season_fallback_used": 0,
    }
