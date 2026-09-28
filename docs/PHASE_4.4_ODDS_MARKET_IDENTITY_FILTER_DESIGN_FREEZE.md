# PHASE 4.4 - ODDS MARKET IDENTITY & FILTER DESIGN FREEZE

## Status

**Status:** FROZEN
**Phase:** 4.4
**Scope:** Design only
**Implementation rule:** Phase 5 must implement this document exactly. If implementation discovers a contradiction, implementation must stop and return the contradiction for a new design decision.

This document is the authoritative design freeze for provider-market identity, market classification, filtering, selection/line identity, duplicate detection, and their interaction with the existing Fover Odds snapshot pipeline.

No Python source, database schema, migration, runtime data, scheduler behavior, or provider sync is changed by this phase.

## 1. Evidence Boundary

### 1.1 Repository evidence reviewed

The following repository documents and source surfaces were available and reviewed:

- `docs/PHASE_0_ODDS_ARCHITECTURE_SCOPE_FREEZE.md`
- `docs/PHASE_1_ODDS_PROVIDER_API_AUDIT_REPORT.md`
- `docs/PHASE_2_ODDS_IDENTITY_DATA_MAPPING_IMPLEMENTATION_REPORT.md`
- `docs/PHASE_3_ODDS_SYNC_PIPELINE_IMPLEMENTATION_REPORT.md`
- `docs/ODDS_AUTO_SYNC_RUNTIME_VERIFICATION_REPORT.md`
- `docs/ODDS_AUTO_SYNC_REAL_RUNTIME_PROOF_REPORT.md`
- `docs/ODDS_72H_AUTO_SYNC_RUNTIME_VERIFICATION_REPORT.md`
- `app/providers/odds_provider.py`
- `app/services/odds_identity.py`
- `app/services/odds_service.py`
- `app/services/odds_sync_service.py`
- `app/repositories/odds_repository.py`
- `app/models/odds.py`
- `app/services/scheduler.py`
- `tests/test_odds_identity_mapping.py`

The expected Odds-specific Phase 4.1 duplicate-selection root-cause report, Phase 4.2 provider-payload evidence report, and Phase 4.3 parsing/normalization root-cause trace were **not found in the repository**. They are therefore unavailable as repository files and are not represented as independently verified reports here.

This freeze does use the confirmed Phase 4.3 evidence supplied with the Phase 4.4 request. No additional provider behavior is inferred from that evidence.

### 1.2 Confirmed provider evidence

For provider fixture `1528902`, the confirmed payload evidence is:

| Provider market ID | Raw provider name | Confirmed meaning |
|---|---|---|
| `5` | `Goals Over/Under` | Goals over/under |
| `45` | `Corners Over Under` | Corners over/under |

The evidence includes bookmaker names `Betfair` and `1xBet`, and selections `Over 4.5`, `Under 4.5`, `Over 5.5`, `Under 5.5`, `Over 6.5`, and `Under 6.5`.

The provider data is valid. The application currently maps both market IDs `5` and `45` to `Goals Over/Under`. Therefore, for example, these valid provider records are different markets:

```text
Betfair / market 5  / Over 4.5 -> 8.00
Betfair / market 45 / Over 4.5 -> 1.01
```

The eight prior `duplicate_selection` rejections involving these same selection values were false application collisions, not provider duplicates. The frozen classification is:

`APPLICATION_IDENTITY_COLLISION`

## 2. Existing Architecture and Business Requirement

The existing architecture remains:

```text
Provider
    -> OddsSyncService
    -> Repository
    -> Database
```

The existing business requirement is preserved. Fover receives provider markets, filters them to the supported business slice, persists the accepted snapshot, and calculates Myanmar Odds only for the existing eligible markets. This design does not add a new betting product or redesign the Myanmar Odds formula.

The current source evidence establishes these provider market IDs as the supported market set:

| Provider market ID | Frozen Fover market type | Provider data status | Current Fover output |
|---|---|---|---|
| `1` | `MATCH_WINNER` | Accepted | Persisted when complete and in the configured bookmaker slice; not Myanmar Odds eligible |
| `4` | `ASIAN_HANDICAP` | Accepted | Persisted when complete; Myanmar Odds eligible |
| `5` | `GOALS_OVER_UNDER` | Accepted | Persisted when complete; Myanmar Odds eligible |
| `45` | `CORNERS_OVER_UNDER` | Accepted as a distinct market | Persisted only if the accepted snapshot/output contract includes it; never Myanmar Odds eligible |

Current repository evidence also says the active Odds path selects bookmaker ID `11` (`1xBet`) for the existing configured Fover output. The confirmed `Betfair` rows prove provider payload diversity and the collision, but do not prove that Betfair is part of the current persisted Fover bookmaker output. Expanding persistence to additional bookmakers is therefore **DESIGN DECISION REQUIRED** and is outside this freeze.

Provider markets other than IDs `1`, `4`, `5`, and `45` are provider data but are not part of the current Fover business output. They are ignored before canonical duplicate detection.

## 3. Frozen Market Identity

### 3.1 Identity components and roles

| Component | Frozen role |
|---|---|
| Local Match ID | Required local business boundary. It binds every accepted row to the local `Match` and is the persisted `fixture_id`. |
| Provider fixture ID | External identity used for the provider request and response validation. It must match the requested fixture before normalization. It is not interchangeable with the local Match ID. |
| Bookmaker identity | Provider bookmaker identity is validated from the provider bookmaker record. Current persistence remains limited to the configured bookmaker slice, whose existing source evidence is bookmaker ID `11` / `1xBet`. The canonical comparison uses normalized bookmaker identity/name. |
| Provider market ID | Authoritative provider-market discriminator during validation and classification. It must be preserved until the canonical market type is established. IDs `5` and `45` must never be collapsed before classification. |
| Normalized market type | Canonical Fover market component: `MATCH_WINNER`, `ASIAN_HANDICAP`, `GOALS_OVER_UNDER`, or `CORNERS_OVER_UNDER`. It is the persisted market distinction for the current schema. |
| Selection | Deterministic normalized outcome side/value, such as `HOME`, `DRAW`, `AWAY`, `OVER`, or `UNDER`. |
| Line / handicap | Separate canonical identity component when the provider value carries a point, such as `4.5`, `5.5`, `6.5`, `+0.75`, or `-1.25`. Empty only for markets without a line. |
| Provider selection ID | Optional provider source metadata. If present, retain it through normalization for traceability and use it as a tie-breaker only where the provider contract proves it is needed. It is not required for the confirmed payload evidence and is not independently persisted by the current model. |

The provider market ID is not discarded before classification. The canonical business identity is established only after provider identity validation, classification, filtering, and selection/line normalization.

### 3.2 Canonical business identity

For an accepted record, the frozen canonical business identity is:

```text
(
    local_match_id,
    normalized_bookmaker_identity,
    normalized_fover_market_type,
    normalized_selection,
    normalized_line_or_handicap
)
```

The provider fixture ID and provider market ID are mandatory inputs to establish this key. For the currently accepted IDs, classification is one-to-one, so the normalized market type carries the persisted distinction. The numeric odd value is not part of identity; changing an odd updates the same record.

The current database constraint `(fixture_id, bookmaker_name, market_name, selection)` remains compatible because the normalized line is retained in the selection representation already used by the current model, and the market name is changed by classification to the distinct canonical type. If a future provider contract requires a separately persisted provider market ID, provider selection ID, or line column, that is a new design decision and migration, not an implicit Phase 4.4 change.

Therefore:

```text
Goals Over/Under + Over 4.5
    != Corners Over/Under + Over 4.5
```

even when local match, bookmaker, selection text, and line are identical.

## 4. Deterministic Classification

Classification happens from provider identity before final duplicate detection:

```text
Provider market ID
        |
        v
Market classification
        |
        v
Fover market type
```

The frozen mapping is:

```text
1  -> MATCH_WINNER
4  -> ASIAN_HANDICAP
5  -> GOALS_OVER_UNDER
45 -> CORNERS_OVER_UNDER
```

Raw market names may be retained as evidence and used for validation, but a raw name must not override a known provider market ID. In particular, market ID `45` must not be normalized to `Goals Over/Under`; its Fover type is `CORNERS_OVER_UNDER`.

Classification is not a display-label rename. It preserves the semantic distinction that goals and corners are different markets and gives them different canonical identity components and different Myanmar Odds eligibility.

## 5. Frozen Market Filter

Filtering occurs before final canonical duplicate detection.

### 5.1 Accepted markets

- Market ID `1`: Match Winner.
- Market ID `4`: Asian Handicap.
- Market ID `5`: Goals Over/Under.
- Market ID `45`: Corners Over/Under.

An accepted market is eligible for normalization only when its provider identity, bookmaker context, values, selections, lines, and odds satisfy the validation rules below. Accepted does not mean every provider bookmaker is newly persisted; the existing bookmaker scope remains in force.

### 5.2 Ignored markets

Unknown or unsupported provider market IDs are `IGNORE MARKET`. They produce no canonical record and must not participate in duplicate detection. They must not be mapped by raw-name similarity into a supported Fover market.

If the provider sends only ignored markets and no accepted rows, the existing empty/invalid snapshot policy applies: the snapshot is rejected and no replacement is persisted.

### 5.3 Persistence and calculation

- Complete accepted rows in the configured current bookmaker slice are persisted as one Odds snapshot.
- Match Winner is persisted when complete but does not participate in Myanmar Odds calculation.
- Asian Handicap is persisted and participates in Myanmar Odds calculation.
- Goals Over/Under is persisted and participates in Myanmar Odds calculation.
- Corners Over/Under is an accepted, persistable market when its complete records are present in the configured bookmaker slice; it never participates in the Goals Over/Under Myanmar Odds path.
- Whether additional bookmaker identities such as Betfair are persisted is **DESIGN DECISION REQUIRED**; this freeze does not expand the existing 1xBet scope.

## 6. Selection and Line Identity

Selection and line normalization is deterministic and occurs after classification.

### 6.1 Over/under selections

When the provider embeds the line in the value, split the value into:

```text
selection side: OVER or UNDER
line: canonical numeric/text point, e.g. 4.5, 5.5, 6.5
```

The canonical identity distinguishes:

```text
OVER + 4.5 != OVER + 5.5
UNDER + 4.5 != UNDER + 5.5
```

The line must be retained in the persisted selection representation because the current `Odds` model has no separate line column. The normalization must not discard the line while extracting the side.

### 6.2 Asian Handicap selections

For values such as `Home -0.75` or `Away +0.75`, normalize the side and signed handicap point deterministically. The sign and point are identity-bearing. Equivalent formatting may normalize to one representation; different points must remain different.

### 6.3 Match Winner selections

Normalize supported outcomes to `HOME`, `DRAW`, and `AWAY`. Match Winner has no line component.

### 6.4 Missing or malformed line

A line-required selection without a parseable line is an incomplete accepted record. The record is rejected and the overall snapshot is rejected under the atomic snapshot rule. It is never silently assigned a default line.

## 7. Myanmar Odds Design

The existing Myanmar Odds formula and calculation algorithm remain unchanged.

The frozen eligibility routing is:

```text
ASIAN_HANDICAP
        |
        v
existing Myanmar Odds calculation
```

and:

```text
GOALS_OVER_UNDER
        |
        v
existing Myanmar Odds calculation
```

`MATCH_WINNER` is not part of Myanmar Odds calculation.

`CORNERS_OVER_UNDER` is not part of Myanmar Odds calculation. It must be excluded by normalized market type, not merely by a display-name comparison. Thus market ID `45` cannot enter the Goals Over/Under calculation even when its selection is `Over 4.5` or `Under 4.5`.

No formula, favorite-team rule, handicap parser algorithm, or Myanmar Odds label algorithm is redesigned in Phase 4.4.

## 8. Frozen Normalization Flow

```text
Provider Raw Response
        |
        v
Provider Identity Validation
        |
        v
Market Identity Preservation
        |
        v
Market Classification
        |
        v
Market Filter
        |
        v
Selection / Line Normalization
        |
        v
Canonical Business Identity
        |
        v
Duplicate Detection
        |
        v
Myanmar Odds Calculation
        |
        v
Normalized Snapshot
        |
        v
Persistence
```

Responsibilities:

1. **Provider Raw Response:** receive the provider payload without inventing missing values.
2. **Provider Identity Validation:** verify payload shape and that response fixture ID equals the requested provider fixture ID.
3. **Market Identity Preservation:** retain provider market ID and bookmaker context before any local naming.
4. **Market Classification:** map the provider ID to exactly one frozen Fover market type.
5. **Market Filter:** accept only the frozen market set; ignore unsupported markets without making them collide.
6. **Selection / Line Normalization:** normalize outcome side and line/handicap deterministically.
7. **Canonical Business Identity:** build the key from local match, bookmaker, normalized market type, selection, and line.
8. **Duplicate Detection:** reject only equal canonical identities within the accepted market slice.
9. **Myanmar Odds Calculation:** calculate only for Asian Handicap and Goals Over/Under using the unchanged service logic.
10. **Normalized Snapshot:** require a complete valid accepted snapshot, or reject the entire snapshot.
11. **Persistence:** replace the fixture snapshot through the existing repository and outer transaction.

## 9. Duplicate Rule

Two records are genuine duplicates only when their canonical business identities are equal:

```text
same local match
same bookmaker identity
same normalized Fover market type
same normalized selection
same normalized line/handicap
```

Same bookmaker, same selection, and same line are not sufficient when provider market identity classifies them into different Fover market types.

Therefore:

```text
Betfair / market 5  / Over 4.5
Betfair / market 45 / Over 4.5
```

are not duplicates. They become:

```text
Betfair / GOALS_OVER_UNDER   / OVER + 4.5
Betfair / CORNERS_OVER_UNDER / OVER + 4.5
```

and must not be rejected as `duplicate_selection`. A repeated `Betfair / market 5 / Over 4.5` with the same local match and line is a genuine duplicate and is rejected deterministically.

## 10. Snapshot Safety

The existing all-or-nothing contract remains frozen:

```text
VALID SNAPSHOT
    -> persist complete snapshot

INVALID / PARTIAL SNAPSHOT
    -> reject entire snapshot
    -> no partial persistence
```

An ignored unsupported market does not make a snapshot partial by itself. A malformed accepted market, invalid accepted record, fixture mismatch, duplicate canonical identity, or incomplete accepted snapshot makes the overall sync invalid under the existing `PARTIAL_ODDS_SNAPSHOT`/validation behavior. Existing data is not replaced by a partial result.

## 11. Failure Semantics

| Condition | Outcome | Snapshot effect |
|---|---|---|
| Unknown provider market ID | `IGNORE MARKET` | Continue evaluating other markets; no collision key is created |
| Unsupported provider market | `IGNORE MARKET` | Same as unknown market; it cannot enter supported Myanmar Odds paths |
| Malformed market object | `REJECT RECORD` | Accepted snapshot becomes invalid; `REJECT SNAPSHOT` and persist nothing |
| Missing bookmaker identity/name | `REJECT RECORD` | `REJECT SNAPSHOT` if it is in the accepted input slice |
| Bookmaker outside current configured persistence scope | `IGNORE MARKET`/bookmaker slice | Do not expand bookmaker persistence in this phase |
| Missing selection | `REJECT RECORD` | `REJECT SNAPSHOT` |
| Missing line where the market requires one | `REJECT RECORD` | `REJECT SNAPSHOT` |
| Malformed line/handicap | `REJECT RECORD` | `REJECT SNAPSHOT` |
| Invalid or missing odds value | `REJECT RECORD` | `REJECT SNAPSHOT` |
| Duplicate within the same classified market | `REJECT RECORD` | `REJECT SNAPSHOT` |
| Same selection/line across different classified markets | `ACCEPT` | Distinct canonical records; no false duplicate |
| Provider fixture ID missing or mismatched | `REJECT SNAPSHOT` | No persistence |
| Incomplete provider snapshot for an accepted market | `REJECT SNAPSHOT` | No partial persistence |
| Valid complete accepted snapshot | `ACCEPT` | Persist complete replacement after outer commit |

The `REJECT RECORD` label describes the local normalization finding. Because snapshot atomicity is mandatory, the containing sync outcome is `REJECT SNAPSHOT` whenever an accepted record is rejected.

## 12. Before / After

### Current behavior

```text
Provider Market 5
        |
        v
Goals Over/Under

Provider Market 45
        |
        v
Goals Over/Under
        |
        v
FALSE DUPLICATE
        |
        v
PARTIAL_ODDS_SNAPSHOT
```

### Frozen target behavior

```text
Provider Market 5
        |
        v
GOALS_OVER_UNDER

Provider Market 45
        |
        v
CORNERS_OVER_UNDER

        |
        v
Distinct Market Identity
        |
        v
No false duplicate collision
```

## 13. Architecture Boundaries

The existing boundaries remain unchanged:

- **Provider:** transport/read-only provider request using `provider_fixture_id`.
- **OddsSyncService:** identity validation, classification orchestration, normalization orchestration, snapshot validation, and repository coordination. It does not commit or roll back.
- **Repository:** SQL and persistence only. It does not classify provider payloads, call the provider, commit, roll back, or invalidate cache.
- **API:** owns commit/rollback for manual sync.
- **Scheduler:** owns commit/rollback for scheduled sync.
- **Cache:** invalidates only after successful commit.
- **Lock:** existing match-scoped shared resource lock remains the single lock boundary.

No second Odds persistence path is introduced. Manual sync and scheduler sync use the same normalization/classification/filter rules through `OddsSyncService`.

## 14. Database Impact

**No database migration required.**

The existing `Odds` model can distinguish the two accepted markets through the persisted canonical market value (`GOALS_OVER_UNDER` versus `CORNERS_OVER_UNDER`) and the existing uniqueness boundary. The current schema does not persist provider market ID or provider selection ID as separate columns, so those values remain validation/classification inputs and transient source identity in this design.

This decision is limited to the confirmed accepted market mapping. If Phase 5 proves that raw provider market IDs, provider selection IDs, or a dedicated line column must be independently queried or persisted, that is a contradiction with this freeze. Phase 5 must stop and request a new design decision; it must not add a migration silently.

No migration is created or applied in Phase 4.4.

## 15. Scheduler, Cache, and Lock Impact

### Scheduler

The scheduler continues to resolve local Match identity and call `OddsSyncService`. It does not gain a scheduler-specific market normalizer. Manual sync and scheduler sync therefore share the same market identity and filter behavior.

### Lock

Market identity changes do not change the lock scope. The existing match-scoped shared lock remains the lock for one local Match Odds snapshot. No market-specific lock is introduced.

### Cache

The existing Odds cache key remains match-scoped (`match / local_match_id / odds`). It is not expanded by provider market, bookmaker, or selection. Cache invalidation remains after successful outer commit; failed commit or rejected snapshot does not trigger a successful-snapshot invalidation. The existing pending invalidation recovery behavior remains in force.

## 16. Transaction and Persistence Safety

The target write sequence remains:

```text
API or Scheduler begins transaction
        -> OddsSyncService orchestrates
        -> Repository replaces the local Match snapshot
        -> flush
        -> API or Scheduler commits
        -> cache invalidates after successful commit
```

On validation failure, persistence failure, analytics failure, or commit failure, the existing outer owner rolls back where the outcome is known. An ambiguous commit follows the existing verification path and is not blindly retried. The market identity design does not weaken snapshot atomicity or alter transaction ownership.

## 17. Design Decision Table

| Question | Frozen Decision | Evidence | Reason |
|---|---|---|---|
| Provider market identity | Preserve provider market ID through validation and classification | Phase 1/2 reports; current normalizer; confirmed fixture `1528902` evidence | IDs `5` and `45` represent different valid markets |
| Market classification | `1 -> MATCH_WINNER`, `4 -> ASIAN_HANDICAP`, `5 -> GOALS_OVER_UNDER`, `45 -> CORNERS_OVER_UNDER` | Current supported IDs plus confirmed Phase 4.3 evidence | Prevents semantic market collision |
| Market filtering | Accept IDs `1,4,5,45`; ignore all unsupported/unknown markets | Phase 1 report and current `_is_target_market()` | Preserves existing Fover market scope |
| Canonical key | Local Match, normalized bookmaker, normalized Fover market type, normalized selection, normalized line/handicap | Phase 2 identity report and current schema | Odd value changes must update, not create identity |
| Selection identity | Normalize outcome side/value; keep meaningful value distinctions | Phase 2 report and current tests | Makes repeated provider values deterministic |
| Line identity | Preserve embedded line as a separate canonical component and in current selection representation | Confirmed values `4.5`, `5.5`, `6.5`; current model has no line column | Ensures `Over 4.5 != Over 5.5` without inventing a column |
| Myanmar Odds eligibility | Asian Handicap and Goals Over/Under only | Current `OddsService._attach_myanmar_odd_labels()` | Preserves existing formula and routing |
| Corners Myanmar Odds | Never eligible for Goals Over/Under calculation | Market ID `45` evidence and semantic classification | Prevents corners from entering goals logic |
| Duplicate detection | Compare canonical identities only after classification | Phase 2 report; confirmed false collisions | Same selection across different markets is not duplicate |
| Snapshot rejection | Any invalid accepted record rejects the entire snapshot; no partial persistence | Phase 3 report and `PARTIAL_ODDS_SNAPSHOT` path | Preserves all-or-nothing safety |
| Database migration | No database migration required | Current `Odds` model and unique constraint | Distinct canonical market values fit existing schema |
| Cache behavior | Keep local-match Odds key; invalidate after successful commit | Phase 1/3 reports and scheduler source | Market identity does not change snapshot cache scope |
| Lock behavior | Keep existing match-scoped shared lock | Phase 3 report and scheduler source | One lock protects one Match snapshot |
| Scheduler behavior | Scheduler continues to call the shared `OddsSyncService` path | Phase 0/1/3 reports and scheduler source | Prevents a second normalization path |
| Bookmaker scope | Preserve current configured 1xBet persistence scope; additional bookmaker persistence is design decision required | Phase 1 report; current `_get_1xbet_bookmaker()`; confirmed Betfair evidence | Do not invent a new bookmaker business requirement |
| Provider fixture identity | Validate provider response fixture against requested provider fixture and bind output to local Match | Phase 1/2/3 reports and current sync service | Prevents cross-fixture persistence |

## 18. Acceptance Proof

This freeze explicitly satisfies the required design properties:

1. Goals and corners cannot collide because IDs `5` and `45` classify to different canonical market types.
2. Provider market identity is preserved until canonical identity is established.
3. Selection and line identity are deterministic; `Over 4.5` differs from `Over 5.5`.
4. Equal canonical identities remain genuine duplicates.
5. Unsupported markets are ignored deterministically before duplicate detection.
6. Asian Handicap Myanmar Odds logic remains unchanged.
7. Goals Over/Under Myanmar Odds logic remains unchanged.
8. Corners Over/Under cannot enter the Goals Over/Under Myanmar Odds path.
9. Snapshot atomicity remains unchanged.
10. Transaction ownership remains API/Scheduler-owned.
11. Match-scoped locking remains unchanged.
12. Scheduler and manual sync use the same service identity/filter path.
13. Database impact is explicit: no database migration required.
14. No speculative second persistence path or unrelated architecture change is required.

## 19. Freeze Rule

Phase 5 must implement this document exactly. In particular, it must not:

- map provider market `45` to Goals Over/Under;
- run duplicate detection before market classification;
- use selection text alone as market identity;
- allow Corners Over/Under into Goals Over/Under Myanmar Odds calculation;
- persist an incomplete accepted snapshot;
- move commit/rollback into `OddsSyncService` or the repository;
- create a scheduler-specific normalization path;
- add a schema migration without a new design decision.

If implementation reveals that the existing schema cannot preserve the frozen canonical identity, or that a required provider field is unavailable, implementation must stop and return the exact contradiction for a new design decision.

**PHASE 4.4 DESIGN FREEZE - COMPLETE**