from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

import app.services.scheduler as scheduler_module
from app.services.odds_reconciliation import (
    COMMIT_PENDING,
    snapshot_fingerprint,
)
from app.services.odds_sync_service import (
    COMMIT_CONFIRMED,
    COMMIT_NOT_APPLIED,
    COMMIT_UNKNOWN,
    IDENTITY_REJECTED,
    IDENTITY_UNAVAILABLE,
    OddsIdentityLookupError,
    OddsMatchIdentityError,
    OddsSyncService,
)
from app.services.scheduler import commit_odds_refresh


MATCH_ID = 42
PROVIDER_FIXTURE_ID = 4200
OBSERVED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def expected_odds_row():
    return {
        "fixture_id": MATCH_ID,
        "bookmaker_name": "1xBet",
        "market_name": "ASIAN_HANDICAP",
        "selection": "Home -0.25",
        "odd_value": "1.800000",
        "myanmar_odd": None,
        "giving_team": "HOME",
        "last_updated": OBSERVED_AT,
    }


def odds_row(*, value="1.800000", updated_at=OBSERVED_AT):
    return SimpleNamespace(
        fixture_id=MATCH_ID,
        bookmaker_name="1xBet",
        market_name="ASIAN_HANDICAP",
        selection="Home -0.25",
        odd_value=value,
        myanmar_odd=None,
        giving_team="HOME",
        last_updated=updated_at,
    )


def analytics_row(*, value="1.800000", observed_at=OBSERVED_AT):
    return SimpleNamespace(
        match_id=MATCH_ID,
        bookmaker_name="1xBet",
        market_name="ASIAN_HANDICAP",
        selection="Home -0.25",
        odd_value=Decimal(value),
        myanmar_odd=None,
        giving_team="HOME",
        observed_at=observed_at,
        source_provider="api-football",
    )


class OddsRepository:
    def __init__(self, rows):
        self.rows = rows

    async def get_fixture_odds(self, _db, _match_id):
        return self.rows


class AnalyticsOddsRepository:
    def __init__(self, rows):
        self.rows = rows

    async def list_by_match(self, _db, _match_id):
        return self.rows


class RetryRepository:
    def __init__(self, state):
        self.state = state

    async def get_by_match_id(self, _db, _match_id):
        return self.state


def service_for(odds_rows, analytics_rows):
    candidate = [expected_odds_row()]
    state = SimpleNamespace(
        match_id=MATCH_ID,
        provider_fixture_id=PROVIDER_FIXTURE_ID,
        attempt_id="attempt-1",
        reconciliation_state=COMMIT_PENDING,
        candidate_fingerprint=snapshot_fingerprint(candidate),
        pre_attempt_odds_fingerprint=snapshot_fingerprint([]),
        pre_attempt_analytics_fingerprint=snapshot_fingerprint([]),
    )
    analytics_service = SimpleNamespace(
        odds_repository=AnalyticsOddsRepository(analytics_rows)
    )
    service = OddsSyncService(
        odds_service=SimpleNamespace(client=None),
        odds_repository=OddsRepository(odds_rows),
        analytics_projection_service=analytics_service,
    )
    service.retry_state_repository = RetryRepository(state)
    db = SimpleNamespace(
        scalar=AsyncMock(
            return_value=SimpleNamespace(
                local_match_id=MATCH_ID,
                provider="api-football",
                provider_fixture_id=PROVIDER_FIXTURE_ID,
            )
        )
    )
    return service, db


@pytest.mark.asyncio
async def test_ambiguous_commit_confirms_only_when_both_snapshots_match():
    service, db = service_for(
        [odds_row()],
        [analytics_row()],
    )

    result = await service.verify_commit_unknown(
        db,
        MATCH_ID,
        PROVIDER_FIXTURE_ID,
        [expected_odds_row()],
    )

    assert result == {"state": COMMIT_CONFIRMED, "reason": "BOTH_CANDIDATE_SNAPSHOTS_MATCH"}


@pytest.mark.asyncio
async def test_ambiguous_commit_rejects_giving_team_mismatch():
    odds = odds_row()
    analytics = analytics_row()
    analytics.giving_team = "AWAY"
    service, db = service_for([odds], [analytics])

    result = await service.verify_commit_unknown(
        db,
        MATCH_ID,
        PROVIDER_FIXTURE_ID,
        [expected_odds_row()],
    )

    assert result["state"] == COMMIT_UNKNOWN


@pytest.mark.parametrize(
    ("odds_rows", "analytics_rows"),
    [
        ([odds_row()], []),
        ([], [analytics_row()]),
    ],
    ids=["odds-only", "analytics-only"],
)
@pytest.mark.asyncio
async def test_one_sided_snapshot_match_remains_unknown(odds_rows, analytics_rows):
    service, db = service_for(odds_rows, analytics_rows)

    result = await service.verify_commit_unknown(
        db,
        MATCH_ID,
        PROVIDER_FIXTURE_ID,
        [expected_odds_row()],
    )

    assert result["state"] == COMMIT_UNKNOWN


@pytest.mark.asyncio
async def test_both_pre_attempt_snapshots_prove_non_application():
    service, db = service_for([], [])

    result = await service.verify_commit_unknown(
        db,
        MATCH_ID,
        PROVIDER_FIXTURE_ID,
        [expected_odds_row()],
    )

    assert result == {
        "state": COMMIT_NOT_APPLIED,
        "reason": "BOTH_PRE_ATTEMPT_SNAPSHOTS_MATCH",
    }


@pytest.mark.asyncio
async def test_neither_snapshot_matching_unexpected_content_remains_unknown():
    later = OBSERVED_AT + timedelta(minutes=1)
    service, db = service_for(
        [odds_row(value="1.70", updated_at=later)],
        [analytics_row(value="1.70", observed_at=later)],
    )

    result = await service.verify_commit_unknown(
        db,
        MATCH_ID,
        PROVIDER_FIXTURE_ID,
        [expected_odds_row()],
    )

    assert result["state"] == COMMIT_UNKNOWN


@pytest.mark.asyncio
async def test_fresh_session_verification_error_is_not_converted_to_success():
    service = OddsSyncService(odds_service=SimpleNamespace(client=None))
    db = SimpleNamespace(scalar=AsyncMock(side_effect=RuntimeError("verification unavailable")))

    with pytest.raises(RuntimeError, match="verification unavailable"):
        await service.reconcile_refresh_attempt(
            db,
            SimpleNamespace(
                match_id=MATCH_ID,
                provider_fixture_id=PROVIDER_FIXTURE_ID,
                candidate_fingerprint=None,
                pre_attempt_odds_fingerprint=snapshot_fingerprint([]),
                pre_attempt_analytics_fingerprint=snapshot_fingerprint([]),
            ),
        )


@pytest.mark.asyncio
async def test_attempt_preimage_evidence_is_persisted_without_service_commit():
    odds_snapshot = [odds_row(value="1.70", updated_at=OBSERVED_AT)]
    analytics_snapshot = [analytics_row(value="1.70", observed_at=OBSERVED_AT)]
    persisted = {}

    class StateRepository:
        async def begin_attempt(self, _db, match_id, **values):
            persisted.update(match_id=match_id, **values)

    service = OddsSyncService(
        odds_service=SimpleNamespace(client=None),
        odds_repository=OddsRepository(odds_snapshot),
        analytics_projection_service=SimpleNamespace(
            odds_repository=AnalyticsOddsRepository(analytics_snapshot)
        ),
    )
    service.retry_state_repository = StateRepository()

    attempt_id = await service.prepare_refresh_attempt(
        SimpleNamespace(
            scalar=AsyncMock(
                return_value=SimpleNamespace(
                    provider="api-football",
                    provider_fixture_id=PROVIDER_FIXTURE_ID,
                )
            )
        ),
        MATCH_ID,
        PROVIDER_FIXTURE_ID,
    )

    assert persisted["attempt_id"] == attempt_id
    assert persisted["pre_attempt_odds_fingerprint"] == snapshot_fingerprint(odds_snapshot)
    assert persisted["pre_attempt_analytics_fingerprint"] == snapshot_fingerprint(
        analytics_snapshot
    )
    assert persisted["provider_fixture_id"] == PROVIDER_FIXTURE_ID


class CommitDB:
    def __init__(self, error=None):
        self.error = error
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1
        if self.error:
            raise self.error

    async def rollback(self):
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_confirmed_commit_returns_success_without_recovery_session():
    db = CommitDB()
    service = SimpleNamespace(is_commit_ambiguous=lambda _exc: True)

    result = await commit_odds_refresh(db, service, MATCH_ID, {"status": "CANDIDATE_READY"})

    assert result["state"] == "refreshed"
    assert result["commit_state"] == COMMIT_CONFIRMED
    assert db.commits == 1


@pytest.mark.parametrize(
    ("verification_state", "expected_state"),
    [
        (COMMIT_NOT_APPLIED, COMMIT_NOT_APPLIED),
        (COMMIT_UNKNOWN, COMMIT_UNKNOWN),
    ],
)
@pytest.mark.asyncio
async def test_ambiguous_commit_requires_fresh_verification(
    monkeypatch,
    verification_state,
    expected_state,
):
    db = CommitDB(RuntimeError("connection lost during commit"))
    verification_db = object()
    verification_calls = []
    service = SimpleNamespace(
        is_commit_ambiguous=lambda _exc: True,
        verify_commit_unknown=AsyncMock(
            return_value={"state": verification_state, "reason": "test"}
        ),
    )

    @asynccontextmanager
    async def fresh_session():
        yield verification_db

    monkeypatch.setattr(scheduler_module, "async_session", fresh_session)
    result = await commit_odds_refresh(
        db,
        service,
        MATCH_ID,
        {
            "status": "CANDIDATE_READY",
            "_transaction_context": {
                "provider_fixture_id": PROVIDER_FIXTURE_ID,
                "persistence_rows": [expected_odds_row()],
            },
        },
    )
    verification_calls.append(service.verify_commit_unknown.await_args.args[0])

    assert result["status"] == expected_state
    assert verification_calls == [verification_db]
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_valid_candidate_writes_odds_and_projects_analytics_once():
    order = []
    persisted = []

    provider = SimpleNamespace(
        get_match_odds=AsyncMock(
            return_value={
                "response": [
                    {
                        "fixture": {"id": PROVIDER_FIXTURE_ID},
                        "bookmakers": [
                            {
                                "id": 11,
                                "name": "1xBet",
                                "bets": [
                                    {
                                        "id": 1,
                                        "name": "Match Winner",
                                        "values": [
                                            {"value": "Home", "odd": "1.80"},
                                            {"value": "Draw", "odd": "3.10"},
                                            {"value": "Away", "odd": "4.20"},
                                        ],
                                    },
                                    {
                                        "id": 4,
                                        "name": "Asian Handicap",
                                        "values": [
                                            {"value": "Home -0.5", "odd": "1.91"},
                                            {"value": "Away -0.5", "odd": "1.95"},
                                        ],
                                    },
                                    {
                                        "id": 5,
                                        "name": "Goals Over/Under",
                                        "values": [
                                            {"value": "Over 2.5", "odd": "1.95"},
                                            {"value": "Under 2.5", "odd": "1.90"},
                                        ],
                                    },
                                ],
                            }
                        ],
                    }
                ]
            }
        )
    )

    class CandidateBuilder:
        client = None

        def build_candidate(self, _bookmaker):
            return ([
                {
                    "bookmaker_name": "1xBet",
                    "market_name": market_name,
                    "selection": selection,
                    "odd_value": value,
                    "myanmar_odd": myanmar_odd,
                    "giving_team": "HOME" if market_name == "ASIAN_HANDICAP" else None,
                }
                for market_name, selection, value, myanmar_odd in (
                    ("MATCH_WINNER", "Home", "1.80", None),
                    ("MATCH_WINNER", "Draw", "3.10", None),
                    ("MATCH_WINNER", "Away", "4.20", None),
                    ("ASIAN_HANDICAP", "Home -0.5", "1.91", "HOME"),
                    ("ASIAN_HANDICAP", "Away -0.5", "1.95", "HOME"),
                    ("GOALS_OVER_UNDER", "Over 2.5", "1.95", "OVER"),
                    ("GOALS_OVER_UNDER", "Under 2.5", "1.90", "UNDER"),
                )
            ], [])

        @staticmethod
        def public_market_name(market_name):
            return market_name

    async def replace_fixture_odds(_db, _match_id, rows):
        order.append("odds")
        persisted.extend(rows)

    async def set_candidate_fingerprint(*_args, **_kwargs):
        order.append("candidate")

    repository = SimpleNamespace(replace_fixture_odds=replace_fixture_odds)

    async def project_odds(*_args):
        order.append("analytics")
        return {
            "success": True,
            "accepted_count": 7,
            "analytics_count": 7,
        }

    projection = SimpleNamespace(
        project_odds=project_odds
    )
    db = SimpleNamespace(
        flush=AsyncMock(),
        scalar=AsyncMock(
            return_value=SimpleNamespace(
                local_match_id=MATCH_ID,
                provider="api-football",
                provider_fixture_id=PROVIDER_FIXTURE_ID,
            )
        ),
    )
    service = OddsSyncService(
        CandidateBuilder(),
        odds_provider=provider,
        odds_repository=repository,
        analytics_projection_service=projection,
    )
    service.retry_state_repository = SimpleNamespace(
        set_candidate_fingerprint=set_candidate_fingerprint
    )

    result = await service.refresh_odds(
        db,
        PROVIDER_FIXTURE_ID,
        "odds:test",
        1800,
        local_match_id=MATCH_ID,
        attempt_id="attempt-1",
    )

    assert result["status"] == "CANDIDATE_READY"
    assert order == ["candidate", "odds", "analytics"]
    assert {
        row["giving_team"]
        for row in persisted
        if row["market_name"] == "ASIAN_HANDICAP"
    } == {"HOME"}
    assert {
        row["giving_team"]
        for row in result["odds"]
        if row["market"] == "ASIAN_HANDICAP"
    } == {"HOME"}
    provider.get_match_odds.assert_awaited_once_with(PROVIDER_FIXTURE_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("match", "requested_fixture_id", "reason"),
    [
        (None, PROVIDER_FIXTURE_ID, "MATCH_NOT_FOUND"),
        (
            SimpleNamespace(provider="other-provider", provider_fixture_id=PROVIDER_FIXTURE_ID),
            PROVIDER_FIXTURE_ID,
            "MATCH_PROVIDER_MISMATCH",
        ),
        (
            SimpleNamespace(provider=None, provider_fixture_id=PROVIDER_FIXTURE_ID),
            PROVIDER_FIXTURE_ID,
            "MATCH_PROVIDER_MISMATCH",
        ),
        (
            SimpleNamespace(provider="api-football", provider_fixture_id=None),
            PROVIDER_FIXTURE_ID,
            "INVALID_STORED_PROVIDER_FIXTURE_ID",
        ),
        (
            SimpleNamespace(provider="api-football", provider_fixture_id=True),
            PROVIDER_FIXTURE_ID,
            "INVALID_STORED_PROVIDER_FIXTURE_ID",
        ),
        (
            SimpleNamespace(provider="api-football", provider_fixture_id=0),
            PROVIDER_FIXTURE_ID,
            "INVALID_STORED_PROVIDER_FIXTURE_ID",
        ),
        (
            SimpleNamespace(provider="api-football", provider_fixture_id=-1),
            PROVIDER_FIXTURE_ID,
            "INVALID_STORED_PROVIDER_FIXTURE_ID",
        ),
        (
            SimpleNamespace(provider="api-football", provider_fixture_id="4200"),
            PROVIDER_FIXTURE_ID,
            "INVALID_STORED_PROVIDER_FIXTURE_ID",
        ),
        (
            SimpleNamespace(provider="api-football", provider_fixture_id=PROVIDER_FIXTURE_ID + 1),
            PROVIDER_FIXTURE_ID,
            "PROVIDER_FIXTURE_ID_MISMATCH",
        ),
    ],
)
async def test_identity_validator_rejects_invalid_match_mappings(match, requested_fixture_id, reason):
    service = OddsSyncService(SimpleNamespace(client=None))
    db = SimpleNamespace(scalar=AsyncMock(return_value=match))

    with pytest.raises(OddsMatchIdentityError) as error:
        await service.validate_match_identity(db, MATCH_ID, requested_fixture_id)

    assert error.value.reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_id", [None, True, False, 0, -1, "4200", 4200.0])
async def test_identity_validator_rejects_invalid_requested_fixture_ids(fixture_id):
    service = OddsSyncService(SimpleNamespace(client=None))
    db = SimpleNamespace(scalar=AsyncMock())

    with pytest.raises(OddsMatchIdentityError) as error:
        await service.validate_match_identity(db, MATCH_ID, fixture_id)

    assert error.value.reason == "INVALID_PROVIDER_FIXTURE_ID"
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("local_match_id", [None, True, False, 0, -1, "42", 42.0])
async def test_identity_validator_rejects_invalid_local_match_ids(local_match_id):
    service = OddsSyncService(SimpleNamespace(client=None))
    db = SimpleNamespace(scalar=AsyncMock())

    with pytest.raises(OddsMatchIdentityError) as error:
        await service.validate_match_identity(db, local_match_id, PROVIDER_FIXTURE_ID)

    assert error.value.reason == "INVALID_LOCAL_MATCH_ID"
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_identity_validator_accepts_exact_provider_fixture_mapping():
    service = OddsSyncService(SimpleNamespace(client=None))
    db = SimpleNamespace(
        scalar=AsyncMock(
            return_value=SimpleNamespace(
                provider="api-football",
                provider_fixture_id=PROVIDER_FIXTURE_ID,
            )
        )
    )

    validated_fixture_id = await service.validate_match_identity(
        db,
        MATCH_ID,
        PROVIDER_FIXTURE_ID,
    )

    assert validated_fixture_id == PROVIDER_FIXTURE_ID
    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_identity_lookup_failure_is_not_classified_as_identity_mismatch():
    service = OddsSyncService(SimpleNamespace(client=None))
    lookup_error = RuntimeError("database unavailable")
    db = SimpleNamespace(scalar=AsyncMock(side_effect=lookup_error))

    with pytest.raises(OddsIdentityLookupError) as error:
        await service.validate_match_identity(db, MATCH_ID, PROVIDER_FIXTURE_ID)

    assert error.value.__cause__ is lookup_error
    assert not isinstance(error.value, OddsMatchIdentityError)


@pytest.mark.asyncio
async def test_prepare_attempt_rejects_identity_before_writing_attempt_evidence():
    begin_attempt = AsyncMock()
    service = OddsSyncService(SimpleNamespace(client=None))
    service.retry_state_repository = SimpleNamespace(begin_attempt=begin_attempt)
    service.odds_repository = SimpleNamespace(
        get_fixture_odds=AsyncMock(return_value=[])
    )
    service.analytics_projection_service = SimpleNamespace(
        odds_repository=SimpleNamespace(list_by_match=AsyncMock(return_value=[]))
    )
    db = SimpleNamespace(
        scalar=AsyncMock(
            return_value=SimpleNamespace(
                provider="other-provider",
                provider_fixture_id=PROVIDER_FIXTURE_ID,
            )
        )
    )

    with pytest.raises(OddsMatchIdentityError):
        await service.prepare_refresh_attempt(db, MATCH_ID, PROVIDER_FIXTURE_ID)

    begin_attempt.assert_not_awaited()
    service.odds_repository.get_fixture_odds.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_refresh_rejects_identity_before_provider_and_writes():
    provider = SimpleNamespace(get_match_odds=AsyncMock())
    replace_odds = AsyncMock()
    project_analytics = AsyncMock()
    service = OddsSyncService(
        SimpleNamespace(
            client=None,
            build_candidate=AsyncMock(side_effect=AssertionError("candidate build must not run")),
            public_market_name=lambda value: value,
        ),
        odds_provider=provider,
        odds_repository=SimpleNamespace(replace_fixture_odds=replace_odds),
        analytics_projection_service=SimpleNamespace(project_odds=project_analytics),
    )
    db = SimpleNamespace(
        scalar=AsyncMock(
            return_value=SimpleNamespace(
                provider="other-provider",
                provider_fixture_id=PROVIDER_FIXTURE_ID,
            )
        )
    )

    result = await service.refresh_odds(
        db,
        PROVIDER_FIXTURE_ID,
        "odds:test",
        1800,
        local_match_id=MATCH_ID,
    )

    assert result["status"] == IDENTITY_REJECTED
    assert result["reason"] == "MATCH_PROVIDER_MISMATCH"
    provider.get_match_odds.assert_not_awaited()
    replace_odds.assert_not_awaited()
    project_analytics.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_refresh_lookup_failure_is_infrastructure_outcome_and_fails_closed():
    provider = SimpleNamespace(get_match_odds=AsyncMock())
    service = OddsSyncService(
        SimpleNamespace(
            client=None,
            build_candidate=lambda _bookmaker: ([], []),
            public_market_name=lambda value: value,
        ),
        odds_provider=provider,
    )
    db = SimpleNamespace(scalar=AsyncMock(side_effect=RuntimeError("database unavailable")))

    result = await service.refresh_odds(
        db,
        PROVIDER_FIXTURE_ID,
        "odds:test",
        1800,
        local_match_id=MATCH_ID,
    )

    assert result["status"] == IDENTITY_UNAVAILABLE
    assert result["reason"] == "MATCH_IDENTITY_LOOKUP_FAILED"
    provider.get_match_odds.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_response_fixture_mismatch_preserves_existing_snapshots():
    provider = SimpleNamespace(
        get_match_odds=AsyncMock(
            return_value={
                "response": [
                    {"fixture": {"id": PROVIDER_FIXTURE_ID + 1}, "bookmakers": []}
                ]
            }
        )
    )
    build_candidate = AsyncMock(side_effect=AssertionError("candidate build must not run"))
    existing_odds = [{"market_name": "MATCH_WINNER", "selection": "Home"}]
    existing_analytics = [{"market_name": "MATCH_WINNER", "selection": "Home"}]
    replace_odds = AsyncMock(side_effect=AssertionError("Odds replacement must not run"))
    project_analytics = AsyncMock(side_effect=AssertionError("Analytics projection must not run"))
    service = OddsSyncService(
        SimpleNamespace(
            client=None,
            build_candidate=build_candidate,
            public_market_name=lambda value: value,
        ),
        odds_provider=provider,
        odds_repository=SimpleNamespace(
            rows=existing_odds,
            replace_fixture_odds=replace_odds,
        ),
        analytics_projection_service=SimpleNamespace(
            rows=existing_analytics,
            project_odds=project_analytics,
        ),
    )
    db = SimpleNamespace(
        scalar=AsyncMock(
            return_value=SimpleNamespace(
                provider="api-football",
                provider_fixture_id=PROVIDER_FIXTURE_ID,
            )
        )
    )

    result = await service.refresh_odds(
        db,
        PROVIDER_FIXTURE_ID,
        "odds:test",
        1800,
        local_match_id=MATCH_ID,
    )

    assert result["status"] == IDENTITY_REJECTED
    assert result["reason"] == "PROVIDER_FIXTURE_ID_MISMATCH"
    assert existing_odds == [{"market_name": "MATCH_WINNER", "selection": "Home"}]
    assert existing_analytics == [{"market_name": "MATCH_WINNER", "selection": "Home"}]
    build_candidate.assert_not_awaited()
    replace_odds.assert_not_awaited()
    project_analytics.assert_not_awaited()
