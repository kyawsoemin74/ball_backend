# PHASE 2 - ODDS IDENTITY & DATA MAPPING IMPLEMENTATION REPORT

## 1. Executive Summary

This phase focused strictly on the Odds identity and provider-to-local mapping boundary. The goal was to establish a canonical business identity and a deterministic mapping layer before any later synchronization lifecycle work.

The work completed here is intentionally limited to the Odds domain and does not redesign the larger backend architecture. The frozen baseline remains:

```text
Provider
↓
Service (Read)
↓
SyncService (Write Orchestration)
↓
Repository (Persistence)
↓
PostgreSQL
```

The implementation introduces a deterministic normalization and identity layer in [app/services/odds_identity.py](../app/services/odds_identity.py), with focused tests in [tests/test_odds_identity_mapping.py](../tests/test_odds_identity_mapping.py).

The result is a clear canonical odds identity keyed by:

```text
local_match_id + bookmaker + market + selection + handicap/point
```

The numeric odd value is not treated as part of the business identity. A value change updates the same canonical record rather than creating a new one.

This is a source-level implementation success, but runtime provider verification remains blocked by the Phase 1 environment constraints. Therefore the final classification for PHASE 2 is PARTIAL, not PASS.

---

## 2. Phase 1 Gap Traceability

Phase 1 identified the following confirmed gaps:

- Provider response mapping is PARTIAL.
- Normalization is PARTIAL.
- Bookmaker handling is PARTIAL.
- Market handling is PARTIAL.
- Single-bookmaker hard-coding exists.
- Market support is narrow.
- Retry/rate-limit handling is incomplete.
- Provider test coverage is insufficient.

Phase 2 directly addresses the first four concerns by formalizing canonical identity and mapping behavior. It does not attempt to redesign scheduler runtime, provider retry, or the full sync lifecycle.

---

## 3. Current Odds Identity

The current SQLAlchemy model in [app/models/odds.py](../app/models/odds.py) stores the following local fields:

- `fixture_id` (local match identity)
- `bookmaker_name`
- `market_name`
- `selection`
- `odd_value`
- `myanmar_odd`
- `last_updated`

The business uniqueness constraint is currently:

```text
(fixture_id, bookmaker_name, market_name, selection)
```

This is sufficient for a canonical match/bookmaker/market/selection record but does not yet encode handicap/point as a separately modeled business component. The implementation therefore defines the canonical identity using the available provider semantics while staying compatible with the current model.

---

## 4. Canonical Business Key

The new deterministic canonical key is:

```python
build_odds_business_key(local_match_id, bookmaker_name, market_name, selection, handicap=...)
```

This key is composed as:

```text
(local_match_id, bookmaker.lower(), market.lower(), selection.lower(), handicap.lower())
```

The odd numeric value itself is excluded from the key.

This matches the required business rule:

```text
Same Match + Same Bookmaker + Same Market + Same Selection + Same Point = Same Odds Business Record
```

Changing the odd value from `1.80` to `1.90` does not create a new business record.

---

## 5. Provider Identity Mapping

The provider identity is kept distinct from the local identity.

Provider identity includes:

- provider fixture ID
- bookmaker ID from provider payload
- market/bet ID
- selection value text as provider identity when needed

Local identity includes:

- local `Match.local_match_id`
- local `Odds.id`
- canonical business key for deterministic upsert compatibility

The implementation does not confuse provider IDs with local primary keys. The provider fixture is validated before canonical record creation.

---

## 6. Bookmaker Mapping

The prior design hard-coded a single bookmaker path: the code effectively selected bookmaker ID 11 and named it `1xBet` in the odds filtering. This is a deterministic pattern but limited.

The Phase 2 implementation keeps the current model and does the following:

- accepts a bookmaker when it has a valid name
- rejects missing/empty bookmaker names deterministically
- stores the local bookmaker name as the canonical local bookmaking identity
- preserves the possibility for multiple bookmakers within the current schema in a later phase

The implementation does not silently assume the first bookmaker is canonical. It validates the bookmaker name and rejects incomplete bookmaker data.

---

## 7. Market Mapping

Supported market IDs are explicitly normalized and preserved as the canonical current supported subset:

- `1` => `Match Winner`
- `4` => `Asian Handicap`
- `5` => `Goals Over/Under`
- `45` => `Goals Over/Under`

Additional provider markets are not silently converted into supported markets. If a market is unsupported or malformed, it is ignored or rejected deterministically rather than inserted as a wrong local market.

The implementation therefore enforces a strict supported subset instead of a hidden conversion rule.

---

## 8. Selection Mapping

Selection identity is distinguished from displayed odds value.

Examples:

- Selection: `Home`
- Odd: `1.80`

The value can change while the selection remains the same and the canonical record stays identical.

Selection normalization is deterministic and includes:

- trimming whitespace
- preserving meaningful string values
- using lowercase canonical comparison keys for identity
- rejecting empty or missing selection values

---

## 9. Handicap / Point Normalization

For markets with handicap or point semantics, the implementation normalizes the provider line using the provider text itself.

Examples:

- `Home -0.75`
- `Away +0.75`
- `Over 2.5`
- `Under 2.5`

The canonical identity preserves the sign and point value so equivalent provider inputs do not collapse together incorrectly. This prevents accidental duplication across equivalent but distinct handicap lines.

---

## 10. Odds Value Normalization

Odds value normalization is implemented in [app/services/odds_identity.py](../app/services/odds_identity.py).

Rule set:

- `None` => invalid
- empty string => invalid
- boolean => invalid
- non-numeric string => invalid
- zero and negative values => invalid
- non-finite numbers => invalid
- valid numeric values => converted to float and rejected if <= 0

The normalizer does not silently replace invalid values with `0` or invent missing values.

---

## 11. Provider Response Normalizer

The normalizer is a dedicated pure function layer in [app/services/odds_identity.py](../app/services/odds_identity.py):

- `normalize_odds_value()`
- `build_odds_business_key()`
- `normalize_odds_response()`

This layer is intentionally:

- deterministic
- side-effect free
- independent of database writes
- independent of scheduler/cache
- testable in isolation

It does not commit, read from PostgreSQL, or access cache.

---

## 12. Match Binding

The provider response normalizer validates that:

```text
response.fixture.id == provider_fixture_id
```

and only then builds canonical odds records for the given local match identity. If the provider response does not match the expected fixture ID, the record is rejected as a fixture mismatch.

This prevents orphan odds rows from being created from a wrong provider response.

---

## 13. Duplicate Prevention

Duplicate prevention is built into the normalizer.

The system rejects entries when:

- same bookmaker + market + selection appears more than once in a provider response
- same canonical business key appears repeatedly in processed rows

The selected behavior is deterministic rejection rather than silent duplication.

---

## 14. Upsert Compatibility

The canonical identity is designed to support later synchronization behavior:

```text
Same identity → update the existing row
Changed odd value → same record, updated value
Same response repeated → no new business-record growth
```

This does not implement the full synchronization and commit lifecycle yet. It only prepares the identity layer required by that later pipeline.

---

## 15. Database Constraint Decision

The current database schema already uses a uniqueness constraint on the odds table:

```text
(fixture_id, bookmaker_name, market_name, selection)
```

This matches the canonical business identity expected for the current model within the current scope. No new migration was added in this phase because the existing schema already enforces the key boundary relevant to the phase.

No production data was deleted or rewritten.

---

## 16. Invalid Data Handling

The following deterministic rules are enforced:

- Missing match binding => reject and do not create orphan odds
- Missing bookmaker name => reject
- Missing market name => reject
- Missing selection => reject
- Invalid odd value => reject individual entry without corrupting valid rows
- Duplicate selection => reject
- Malformed payload => reject payload-level processing

The system returns rejected records and reasons rather than silently manufacturing invalid data.

---

## 17. Test Coverage

Focused tests were added in [tests/test_odds_identity_mapping.py](../tests/test_odds_identity_mapping.py) covering:

- identity equality for same match/bookmaker/market/selection
- odd value changes preserve same identity
- selection changes produce different identity
- handicap changes produce different identity
- valid response normalization
- duplicate selection rejection
- missing bookmaker rejection
- invalid odd rejection
- malformed odd normalization failure

These tests are deterministic and do not require a database integration environment.

---

## 18. Regression Results

I ran the focused Odds and scheduler/lock regression set:

```text
cd /d d:/fover_backend ; d:/fover_backend/.venv/Scripts/python.exe -m pytest tests/test_odds_identity_mapping.py tests/test_odds_identity_resolution_bridge.py tests/test_odds_service_myanmar_sync.py tests/test_odds_lock_alignment.py tests/test_scheduler_runtime.py tests/test_resource_lock.py -q
```

Result:

```text
74 passed, 3 warnings in 1.00s
```

The warnings are unrelated Pydantic deprecation warnings in schema models.

---

## 19. Files Changed

- [app/services/odds_identity.py](../app/services/odds_identity.py)
- [tests/test_odds_identity_mapping.py](../tests/test_odds_identity_mapping.py)
- [docs/PHASE_2_ODDS_IDENTITY_DATA_MAPPING_IMPLEMENTATION_REPORT.md](PHASE_2_ODDS_IDENTITY_DATA_MAPPING_IMPLEMENTATION_REPORT.md)

---

## 20. Database Changes

None.

No schema changes, no migration, and no production data changes were made in this phase.

---

## 21. Known Limitations

- Provider runtime verification remains blocked because Phase 1 did not establish an active live provider key and runtime DB environment.
- The current model is still limited to the supported subset of markets and bookmakers.
- The implementation does not yet cover full end-to-end scheduler or repository commit orchestration.
- Retry and rate-limit behavior remain a later phase concern.

---

## 22. PHASE 3 Scope

Phase 3 will address the complete Odds Sync Pipeline, including:

- provider fetch orchestration
- repository write path
- commit/rollback ownership
- post-commit cache invalidation
- scheduler ownership and validation
- end-to-end synchronization behavior

This phase intentionally stops at the canonical identity and mapping boundary.

---

## 23. Final Decision

The identity and mapping foundation required for the later Odds sync lifecycle is implemented and tested. However, runtime provider verification remains blocked from the earlier phase, so this phase is not eligible for PASS classification.

Final classification: PARTIAL
