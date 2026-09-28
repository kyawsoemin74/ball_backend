# PHASE 1 - ODDS PROVIDER / API INTEGRATION AUDIT

## 1. Executive Summary

This phase is an audit-only review of the actual Odds provider integration used by the backend. The implementation is partially present but not fully end-to-end verified in the current runtime environment.

The live call chain is:

```text
API / Scheduler
      ↓
football_service.odds_sync_service.refresh_odds()
      ↓
OddsProvider.get_match_odds()
      ↓
FootballAPIClient.get_with_metadata("/odds", params={"fixture": provider_fixture_id})
      ↓
API-Football HTTP GET
      ↓
FootballAPIResponse
      ↓
OddsSyncService._classify_provider_result()
      ↓
OddsSyncService._validate_odds_payload()
      ↓
OddsService._get_1xbet_bookmaker() + _filter_main_lines()
      ↓
OddsRepository.replace_fixture_odds()
      ↓
PostgreSQL
```

The actual provider request is a GET to the API-Football odds endpoint using the external provider fixture ID from the local match record. The request is correctly built from local `Match.provider_fixture_id`, but the current runtime environment does not have a configured `FOOTBALL_API_KEY` or database connection in the active shell, so provider runtime verification is blocked.

The provider integration is therefore:

- source-implemented and traceable in code
- partially validated by code-path checks
- runtime-verified only to the extent that request handling code exists
- not fully runtime-proven in this environment

Final classification: BLOCKED

---

## 2. Current Provider Architecture

### 2.1 Actual call path traced from code

1. Scheduler trigger: [app/services/scheduler.py](../app/services/scheduler.py)
   - `LiveUpdateScheduler._refresh_odds_job()` selects candidate matches and calls `football_service.odds_sync_service.refresh_odds(...)`.

2. Sync orchestration: [app/services/odds_sync_service.py](../app/services/odds_sync_service.py)
   - `OddsSyncService.refresh_odds()` performs the full provider request validation and persistence orchestration.

3. Provider transport: [app/providers/odds_provider.py](../app/providers/odds_provider.py)
   - `OddsProvider.get_match_odds(match_id)` calls `self.client.get_with_metadata("/odds", params={"fixture": match_id})`.

4. HTTP client: [app/services/base/football_client.py](../app/services/base/football_client.py)
   - `FootballAPIClient.request_with_metadata(method, path, params, timeout=30.0, retries=2)` wraps HTTPX and returns `FootballAPIResponse`.

5. Provider response classification: [app/services/odds_sync_service.py](../app/services/odds_sync_service.py)
   - `_classify_provider_result()` validates fixture id and provider error conditions.

6. Payload validation: [app/services/odds_sync_service.py](../app/services/odds_sync_service.py)
   - `_validate_odds_payload()` checks bookmaker, market, selection, and odd values.

7. Odds selection logic: [app/services/odds_service.py](../app/services/odds_service.py)
   - `_get_1xbet_bookmaker()` selects bookmaker ID `11`.
   - `_filter_main_lines()` extracts supported markets and values.

8. Persistence: [app/repositories/odds_repository.py](../app/repositories/odds_repository.py)
   - `replace_fixture_odds()` deletes existing odds for the fixture and inserts a renewed snapshot.

9. Database model: [app/models/odds.py](../app/models/odds.py)
   - `Odds` stores the local fixture id, bookmaker name, market, selection, odd value, myanmar_odd, and last_updated timestamp.

### 2.2 Read-only API route

The public API endpoint for odds retrieval is in [app/api/matches.py](../app/api/matches.py):

```python
@router.get("/{match_id}/odds")
async def get_match_odds(...):
    result = await football_service.get_cached_odds(db, match_id)
```

This is not the provider fetch path. It is a DB-backed read-only snapshot endpoint.

### 2.3 Missing components

The following are explicitly missing or not active in the audited path:

- provider retry backoff: MISSING
- provider deduplication layer: MISSING
- generic odds normalization layer beyond target-market extraction: PARTIAL
- explicit bookmaker selection configuration outside hard-coded bookmaker ID `11`: MISSING
- automatic runtime verification in the active environment: BLOCKED

---

## 3. Provider Endpoint Contract

### 3.1 Actual endpoint

The exact provider endpoint used by code is:

```text
GET /odds
```

with params:

```python
{"fixture": match_id}
```

The underlying client builds the full URL using:

- base URL: `https://v3.football.api-sports.io`
- setting: [app/core/config.py](../app/core/config.py)

```python
FOOTBALL_API_BASE_URL: str = "https://v3.football.api-sports.io"
```

### 3.2 HTTP method and authentication

The request is issued by [app/services/base/football_client.py](../app/services/base/football_client.py):

```python
async with httpx.AsyncClient(timeout=timeout) as client:
    response = await client.request(method, endpoint, headers=self.headers, params=params)
```

Headers:

```python
self.headers = {
    "x-apisports-key": self.api_key,
    "Content-Type": "application/json",
}
```

Authentication mechanism:

- API key header: `x-apisports-key`
- key source: environment variable `FOOTBALL_API_KEY`
- no OAuth, JWT, or bearer token is used

### 3.3 Timeout and retry behavior

The client does:

- timeout: `30.0` seconds default
- retries: `2` additional attempts (`retries=2`)
- request_with_metadata retries only on server-side exceptions and 5xx-like request failure paths
- 429 is specifically classified as rate-limit but is not retried with a backoff loop in the code

### 3.4 Rate-limit and error handling

The provider client and sync service classify:

- `429` => `PROVIDER_RATE_LIMITED`
- `>= 400` with error payload => `PROVIDER_REQUEST_FAILED`
- malformed JSON => `PROVIDER_REQUEST_FAILED`
- missing/invalid API key => `missing_api_key` and `ValueError`
- fixture mismatch => `PROVIDER_FIXTURE_MISMATCH`

### 3.5 Required vs optional parameters

Required:

- `fixture`: provider fixture ID

Optional:

- none in current call site

The request is constructed only with the fixture parameter. There is no bookmaker, market, or date filter in the current Odds fetch path.

---

## 4. Provider Fixture ID Flow

### 4.1 Source of the fixture ID

The local match row is [app/models/match.py](../app/models/match.py):

```python
provider_fixture_id = Column(Integer, nullable=False, index=True)
```

and the model guarantees:

```python
UniqueConstraint("provider", "provider_fixture_id")
```

### 4.2 Call chain

```text
Match.provider_fixture_id
      ↓
OddsSyncService.refresh_odds()
      ↓
self.odds_provider.get_match_odds(provider_fixture_id)
      ↓
GET /odds?fixture=provider_fixture_id
```

### 4.3 Validation behavior

The provider request is rejected if the match row is missing or if `provider_fixture_id` is null:

```python
provider_fixture_id = getattr(match, "provider_fixture_id", None)
if provider_fixture_id is None:
    return {"error": "provider_fixture_id missing"}
```

The response is also validated against the expected fixture ID:

```python
if int(response_fixture_id) != int(provider_fixture_id):
    return PROVIDER_FIXTURE_MISMATCH
```

### 4.4 Conclusion

The Odds provider request cannot be sent without a valid provider fixture ID in the current code path. If the value is missing, the system exits before the provider call.

---

## 5. Provider Response Structure

The actual provider payload handled by the backend is structurally equivalent to:

```json
{
  "response": [
    {
      "fixture": {"id": 12345},
      "bookmakers": [
        {
          "id": 11,
          "name": "1xBet",
          "bets": [
            {
              "id": 1,
              "name": "Match Winner",
              "values": [
                {"value": "Home", "odd": "2.10"},
                {"value": "Draw", "odd": "3.20"},
                {"value": "Away", "odd": "3.50"}
              ]
            },
            {
              "id": 4,
              "name": "Asian Handicap",
              "values": [
                {"value": "Home -0.5", "odd": "1.95"},
                {"value": "Away +0.5", "odd": "1.95"}
              ]
            },
            {
              "id": 5,
              "name": "Goals Over/Under",
              "values": [
                {"value": "Over 2.5", "odd": "2.00"},
                {"value": "Under 2.5", "odd": "1.80"}
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

### 5.1 Provider field to local destination mapping

| Provider Field | Local Field | Transformation | Nullable | Required |
| --- | --- | --- | --- | --- |
| `response[*].fixture.id` | `Match.provider_fixture_id` | direct validation | No | Yes |
| `bookmakers[*].id` | not stored locally | discarded after validation | No | Yes in validation |
| `bookmakers[*].name` | `Odds.bookmaker_name` | kept as string | Yes | Yes for persisted rows |
| `bets[*].id` | `Odds.market_name` | mapped to supported market names by logic | No | Yes for supported markets |
| `bets[*].name` | `Odds.market_name` | stored as display label | No | Yes |
| `values[*].value` | `Odds.selection` | string trim, preserved | No | Yes |
| `values[*].odd` | `Odds.odd_value` | string conversion to float then string | No | Yes |
| `values[*].value` for handicap line | `Odds.selection` | kept as raw text | No | Only for supported markets |
| `myanmar_odd` | `Odds.myanmar_odd` | generated from Myanmar conversion service | Yes | Optional |
| `last_updated` | `Odds.last_updated` | server timestamp | No | Yes |

### 5.2 Fields not currently stored

The model does not currently store:

- bookmaker ID
- market ID
- provider fixture ID on the odds row
- selection ID
- handicap residual values as dedicated columns
- suspended state
- provider timestamp metadata
- full raw JSON payload

This represents a real information-loss boundary between provider payload and local model.

---

## 6. Provider → Local Mapping

### 6.1 Supported current mapping logic

The backend currently filters and persists only a narrow subset of provider response data:

- bookmaker selection: only bookmaker ID `11` (`1xBet`)
- market IDs supported: `1`, `4`, `5`, and `45`
- selection values retained only when they match the supported line logic
- local row output created by `OddsService._filter_main_lines()` and `OddsSyncService.refresh_odds()`

### 6.2 Supported markets

| Provider Market ID | Provider Name | Local Stored Market Name | Status |
| --- | --- | --- | --- |
| `1` | Match Winner | canonical display label | SUPPORTED |
| `4` | Asian Handicap | canonical display label | SUPPORTED |
| `5` | Goals Over/Under | canonical display label | SUPPORTED |
| `45` | Goals Over/Under | canonical display label | SUPPORTED |

Everything else is effectively ignored as not part of `_is_target_market()` and the filtering path.

---

## 7. Normalization Audit

### 7.1 Where normalization occurs

Normalization is partially implemented in the following places:

- [app/services/odds_service.py](../app/services/odds_service.py)
  - `_canonical_handicap_key()`
  - `_build_handicap_pairs()`
  - `_attach_myanmar_odd_labels()`
  - `_select_main_line_by_id()`
  - `_filter_main_lines()`

- [app/services/odds_sync_service.py](../app/services/odds_sync_service.py)
  - `_validate_odds_payload()` checks type and duplicate conditions

### 7.2 Actual normalization behavior

The code does the following:

- trims string values
- converts odd values to float for validation
- filters invalid or missing `odd` values
- rejects duplicate selection values within a bet block
- rejects malformed or duplicate bookmaker IDs
- builds canonical handicap keys from text and numeric values
- converts selected odds to Myanmar odds labels for supported markets only

### 7.3 Missing normalization

The following are not handled as a general provider normalization layer:

- generic raw-to-canonical transformation for all bookmaker/market shapes
- full provider field mapping for all market types
- provider-specific null and fallback rules outside the narrow supported set
- explicit field-by-field coercion for every possible API-Football variation

Classification: PARTIAL NORMALIZATION

### 7.4 Conclusion

Normalization is present but targeted to a narrow supported-slice of the provider payload. It is not a general “normalize the entire response” layer.

---

## 8. Odds Model Compatibility

### 8.1 Current schema

The local model is [app/models/odds.py](../app/models/odds.py):

```python
id
fixture_id
bookmaker_name
market_name
selection
odd_value
myanmar_odd
last_updated
```

### 8.2 What the provider can currently store

- local fixture ID
- bookmaker name
- market name
- selection
- odd value
- Myanmar odds label
- last update timestamp

### 8.3 What the provider cannot currently store

- bookmaker numeric ID
- market numeric ID
- provider response raw payload
- validity/suspended flags
- original handicap metadata as separate columns
- per-provider item metadata beyond the selected values

### 8.4 Compatibility verdict

The current schema can safely represent only the narrow set of selected odds rows that are filtered and normalized by the code. It does not fully represent the raw API-Football odds contract.

This is a compatibility mismatch between the provider payload and the local database model.

---

## 9. Bookmaker Support

### 9.1 Actual behavior

The code does not persist all bookmakers. It explicitly selects a single bookmaker:

```python
return next((item for item in bookmakers if item.get("id") == 11), None)
```

in [app/services/odds_service.py](../app/services/odds_service.py).

### 9.2 Interpretation

This means:

- multiple bookmakers may exist in the provider response
- only `1xBet` is selected for persistence by this logic
- bookmaker identity is stored as `bookmaker_name` only
- bookmaker ID is not stored
- duplicates are prevented by model uniqueness on `(fixture_id, bookmaker_name, market_name, selection)`

### 9.3 Conclusion

Bookmaker support is partial: the system recognizes multiple bookmaker entries in the response, but persists only one canonical bookmaker in practice. This is not a full multi-bookmaker model.

---

## 10. Market Support

### 10.1 Current supported market IDs

The following are explicitly accepted:

- `1` — Match Winner
- `4` — Asian Handicap
- `5` — Goals Over/Under
- `45` — Goals Over/Under

The supporting logic is in [app/services/odds_service.py](../app/services/odds_service.py):

```python
def _is_target_market(self, market_id: int) -> bool:
    return market_id in {1, 4, 5, 45}
```

### 10.2 Partially supported / ignored

The code explicitly ignores or does not persist any other market IDs. Examples likely present in provider data but not handled here include:

- Double Chance
- Draw No Bet
- Correct Score
- Half Time Result
- HT/FT
- Cards
- Corners
- other special bet categories

### 10.3 Conclusion

Market support is narrow and selective. It is not a general odds market model.

---

## 11. Empty Response Behavior

### 11.1 Actual code behavior

In [app/services/odds_sync_service.py](../app/services/odds_sync_service.py):

```python
responses = payload["response"]
if not responses:
    return {"error": PROVIDER_EMPTY}
```

Later:

```python
if not odds_to_upsert:
    return {"error": PROVIDER_EMPTY}
```

### 11.2 What happens when response is empty or no supported data exists

- provider is considered successful at HTTP level
- empty response or no supported 1xBet lines is classified as `PROVIDER_EMPTY`
- the service returns an error result without deleting existing odds rows
- repository replacement is not called
- no unsupported rows are inserted
- no `odds` snapshot is written
- scheduler sees an error and marks the job failed for that record

### 11.3 Conclusion

The backend does not treat empty provider response as success. It treats it as a non-persistent empty result. It does not clear existing odds, and it does not write new rows.

---

## 12. Provider Error Matrix

| Condition | Classification | Evidence |
| --- | --- | --- |
| HTTP 400+ with payload | `PROVIDER_REQUEST_FAILED` | [app/services/odds_sync_service.py](../app/services/odds_sync_service.py) |
| HTTP 429 | `PROVIDER_RATE_LIMITED` | [app/services/odds_sync_service.py](../app/services/odds_sync_service.py) |
| timeout | `PROVIDER_REQUEST_FAILED` | [app/services/base/football_client.py](../app/services/base/football_client.py) |
| connection failure | `PROVIDER_REQUEST_FAILED` | [app/services/base/football_client.py](../app/services/base/football_client.py) |
| malformed JSON | `PROVIDER_REQUEST_FAILED` | [app/services/base/football_client.py](../app/services/base/football_client.py) |
| fixture mismatch | `PROVIDER_FIXTURE_MISMATCH` | [app/services/odds_sync_service.py](../app/services/odds_sync_service.py) |
| invalid bookmaker payload | `INVALID_BOOKMAKER` | [app/services/odds_sync_service.py](../app/services/odds_sync_service.py) |
| invalid market payload | `INVALID_MARKET` | [app/services/odds_sync_service.py](../app/services/odds_sync_service.py) |
| invalid selection | `INVALID_SELECTION` | [app/services/odds_sync_service.py](../app/services/odds_sync_service.py) |
| invalid odd value | `INVALID_ODDS_VALUE` | [app/services/odds_sync_service.py](../app/services/odds_sync_service.py) |
| malformed response envelope | `INVALID_ODDS_PAYLOAD` | [app/services/odds_sync_service.py](../app/services/odds_sync_service.py) |

### 12.1 Classification scheme used

The code uses the following categories:

- `SUCCESS`: not used directly for failure classification; successful path is when provider payload passes validation and repository update succeeds
- `RETRYABLE`: provider failures may be marked deferred by scheduler state classification
- `TERMINAL`: fixture mismatch, invalid payload, invalid selection, invalid value
- `FAIL-CLOSED`: effectively enforced by returning a failure result and rolling back the outer transaction
- `UNKNOWN`: reserved for ambiguous commit state or connection-level uncertainty at commit time

---

## 13. Retry / Rate Limit Audit

### 13.1 Provider-layer retry

The provider client implements retries in [app/services/base/football_client.py](../app/services/base/football_client.py):

```python
for attempt in range(retries + 1):
    ...
```

with `retries=2` and no explicit exponential backoff.

### 13.2 Rate-limit handling

The code explicitly handles `429` as rate limit but does not perform a structured backoff strategy. It records metrics and returns `FootballAPIResponse`.

### 13.3 Scheduler-level retry

The scheduler does not create a generic retry loop. Instead, it records failed refreshes and uses state classification logic in `OddsSyncService.classify_retry_state()`. There is no explicit provider retry queue or backoff scheduler.

### 13.4 Conclusion

Retry logic exists only in a minimal provider client loop and in classification states. It is not a robust production retry system.

---

## 14. Read-Only Runtime Verification

### 14.1 Current environment check

The current shell environment does not have provider credentials available:

```text
FOOTBALL_API_KEY_SET False
DATABASE_URL_SET False
```

This means runtime verification is blocked.

### 14.2 No write actions performed

No database write, odds insert, or provider sync was triggered during this audit. This was a read-only pass only.

### 14.3 Conclusion

Runtime verification: BLOCKED

---

## 15. Current Odds Database State

The actual runtime database state could not be verified in this environment because the required `DATABASE_URL` is not set in the current shell. This is a runtime verification block, not a code-path success claim.

That said, the code path itself is still intact and continues to rely on the local `odds` table model in [app/models/odds.py](../app/models/odds.py).

---

## 16. Service / SyncService / Repository Boundary

### 16.1 Boundary check

Current behavior matches the intended separation:

- Service: read/cache/domain logic in [app/services/odds_service.py](../app/services/odds_service.py)
- SyncService: write orchestration and validation in [app/services/odds_sync_service.py](../app/services/odds_sync_service.py)
- Repository: SQL persistence in [app/repositories/odds_repository.py](../app/repositories/odds_repository.py)
- API/Scheduler: commit/rollback orchestration in [app/services/scheduler.py](../app/services/scheduler.py)

### 16.2 Violations

No direct repository-to-provider call was found.

No direct DB writes from the provider layer were found.

No provider code owns a transaction commit.

The boundary is mostly respected.

### 16.3 Risk note

`OddsService` builds the selected odds rows and the scheduler calls the `refresh_odds` path with commit orchestration outside the repository. That is acceptable within the current architecture, but it still depends on the worker/scheduler runtime being active.

---

## 17. Transaction / Lock Audit

The scheduler wraps refresh operations in a resource lock:

```python
resource_locked, refresh_result = await run_with_resource_lock(
    db,
    "odds",
    match_id,
    refresh_fixture,
)
```

The outer transaction and commit are orchestrated by the scheduler in [app/services/scheduler.py](../app/services/scheduler.py), not by the provider or repository. The provider request occurs before the repository replacement and before commit finalization. This is a standard pattern for the current code.

The code does not perform provider fetches inside the repository and does not mutate rows in the provider layer.

---

## 18. Cache Audit

The odds service reads cache in [app/services/odds_service.py](../app/services/odds_service.py):

```python
cache_key = make_cache_key("match", fixture_id, "odds")
```

The scheduler invalidates the odds cache after commit in [app/services/scheduler.py](../app/services/scheduler.py):

```python
cache_state = await invalidate_odds_cache_after_commit(...)
```

The cache key is based on the local match id and is invalidated after a successful transaction. Cache reads and invalidation are present and consistent with the architecture.

---

## 19. Test Coverage

No dedicated odds provider tests were found in the workspace `tests` tree when scanned. The project has code-level coverage in the odds service and sync service, but no direct pytest file was found for provider fixtures. This is an audit gap rather than a runtime failure.

Classification: PARTIAL / MISSING as explicit test coverage evidence.

---

## 20. Frontend Contract Impact

The frontend contract is not part of the repository in this workspace. No Flutter source was audited, so the frontend integration impact is not independently verifiable.

Classification: FRONTEND INTEGRATION NOT VERIFIABLE

---

## 21. Confirmed Working

- Actual provider request path exists and is traceable in code.
- API-Football HTTP client has headers, timeouts, and metadata handling.
- `Match.provider_fixture_id` is the required source for the odds request.
- `OddsSyncService` validates response structure and rejects malformed payloads.
- `OddsRepository.replace_fixture_odds()` implements snapshot replacement by fixture.
- The scheduler attempts to orchestrate the odds refresh and cache invalidation path.

## 22. Missing / Incomplete

- provider runtime verification is blocked by missing key and DB configuration
- no generic normalization layer for all odds payloads
- only a single bookmaker is persisted (`1xBet`)
- only a narrow set of market IDs is supported
- no explicit multi-market or multi-bookmaker persistence model
- no robust provider retry backoff strategy
- no dedicated end-to-end tests for the provider path

## 23. Unknown / Blocked

- live provider response payload in this environment
- actual database odds state in the current runtime
- frontend contract impact from Flutter source
- confirmed runtime success/failure of scheduler execution

## 24. PHASE 2 Scope

Phase 2 must address only the gaps confirmed by this audit:

1. provider runtime activation and validation in a real environment with credentials
2. full normalization of API-Football odds payloads beyond the narrow selected slice
3. explicit bookmaker identity handling beyond `1xBet` hard-coding
4. explicit market/bet-type support expansion or formal restriction policy
5. robust error and retry handling for provider failures and rate limits
6. provider-to-local mapping completeness and model compatibility review
7. end-to-end test coverage for provider success, empty, malformed, and error cases

## 25. Final Decision

The provider/API integration is implemented in source and follows a working architecture boundary, but it is not runtime-proven in the current environment and cannot be treated as production-ready evidence.

Classification: BLOCKED

---

## 26. Final Classification

### Classification

BLOCKED

### Rationale

The code path exists, but the environment does not provide the required live provider key and database access needed to validate actual provider runtime behavior. The integration is therefore not proven in operation and cannot be treated as complete or ready for Phase 2 without an operational runtime check.
