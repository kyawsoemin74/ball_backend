# Event Team Canonical Identity Design Freeze

**Phase:** 2 — Design Freeze
**Status:** FROZEN
**Scope:** New Event synchronization and the meaning of `match_events.team_id`

## 1. Problem Statement

API-Football event payloads provide `event.team.id` as a provider Team ID.
Fover Match rows use canonical local Team IDs in `matches.home_team_id` and
`matches.away_team_id`. The current Event path stores the provider ID directly
in `match_events.team_id`, so Event rows and Event API responses can expose an
ID from the wrong identity namespace.

For example, if a provider event has `team.id = 3` and Team Master resolves
provider ID `3` to local `teams.team_id = 122`, then the Event row must use
`team_id = 122`. The numeric relationship is data-driven and must never be
assumed or hard-coded.

## 2. Current Architecture

The observed source flow is:

1. [`app/providers/event_provider.py`](../app/providers/event_provider.py)
   fetches `/fixtures/events` and returns the provider response without
   translating Team identity.
2. [`app/services/event_sync_service.py`](../app/services/event_sync_service.py)
   validates each event's `team.id` as an integer, then resolves player and
   assist identities. It does not resolve Event Team identity.
3. [`app/repositories/event_repository.py`](../app/repositories/event_repository.py)
   persists `event["team"]["id"]` directly as `match_events.team_id`.
4. [`app/services/event_service.py`](../app/services/event_service.py)
   reads persisted rows and serializes `event.team_id` without mapping it.
5. The GET events route in
   [`app/api/matches.py`](../app/api/matches.py) returns that read result.

`EventService.get_match_events` is a provider-payload passthrough, and
`_serialize_api_events` maps provider-shaped `team.id` to flat `team_id`; the
current cached GET route does not use either helper to read Events. They must
not become a substitute for canonical Event synchronization or be used to
repair persisted IDs.

Match synchronization has the needed canonical Team resolution already:
[`app/services/fixture_sync_service.py`](../app/services/fixture_sync_service.py)
uses `TeamService.resolve_provider_teams`, which delegates to
[`app/services/team_sync_service.py`](../app/services/team_sync_service.py).
That service resolves through
[`TeamRepository.find_by_provider_identity`](../app/repositories/team_repository.py),
using provider `"api-football"` and the provider Team ID, and returns the
canonical `Team.team_id`. Fixture sync assigns those local IDs to the Match's
home and away Team fields before persisting the Match.

The current `MatchEvent` ORM model is
[`app/models/match_event.py`](../app/models/match_event.py); its response
schema is [`app/schemas/match_event.py`](../app/schemas/match_event.py).

## 3. Confirmed Root Cause

The Event sync path bypasses the existing Team Master identity lookup. It
accepts the provider's `event.team.id` and hands the unchanged payload to the
repository, which stores that ID in `match_events.team_id`. The read service
then returns the stored value unchanged.

This is an Event write-path identity error, not a Match mapping error or an API
serialization mapping error. There is no team-identity fallback or mapping on
the Event read path.

The admin Event sync response currently also includes `api_events` from the
sync result. Because that collection is based on provider payloads, Phase 3
must ensure it does not echo a provider Team ID in a field presented as the
canonical Event Team identity.

## 4. Canonical Identity Definition

The authority model is frozen as follows:

| Identity | Meaning and authority |
|---|---|
| Provider Team ID | External identity originating from API-Football. It is not a Fover Team ID. |
| Team Master | Canonical authority for Fover Team identity and provider identity association. |
| `teams.team_id` | Canonical local Fover Team ID. |
| `matches.home_team_id` / `matches.away_team_id` | Canonical local Fover Team IDs for the fixture participants. |
| `match_events.team_id` | Canonical local Fover Team ID for the side associated with the Event. |

An Event must never interpret a provider Team ID as a canonical Fover Team ID.

## 5. Provider → Team Master → Canonical Team Flow

The only accepted identity flow for newly synchronized Events is:

```text
API-Football event.team.id
        ↓ provider identity only
TeamRepository.find_by_provider_identity(
    provider="api-football",
    provider_id=<event.team.id>
)
        ↓
Team.team_id
        ↓
EventSyncService canonical Event representation
        ↓
EventRepository
        ↓
match_events.team_id
```

The exact existing repository lookup to reuse is
`TeamRepository.find_by_provider_identity(db, "api-football", provider_team_id)`.
`TeamSyncService.resolve_provider_teams` is the existing service-level batch
wrapper, but the Event path may resolve individual event identities through the
same repository method. Phase 3 must not add a second resolver or a new
identity store. `matches.home_team_id` and `matches.away_team_id` may validate
the resolved identity, but must not be used instead of Team Master resolution.

## 6. Responsibility Boundaries

| Component | Frozen responsibility |
|---|---|
| `EventProvider` | Fetch and preserve the API-Football provider payload. It does not decide Fover canonical identity. |
| `EventSyncService` | Validate provider events; extract provider Team ID; resolve through Team Master; validate Match membership; build canonical Event representations; pass canonical IDs to persistence. |
| `EventRepository` | Persistence only. Persist the explicit canonical Team ID supplied by sync orchestration; never resolve provider identity. |
| `EventService` | Read persisted Event data and return its canonical Team ID. It does not repair or translate IDs. |
| API | Serialize canonical Event data. It does not perform identity correction. |

For defense against accidental regressions, Phase 3 should pass an explicit
canonical Event Team ID to the repository rather than relying on an unchanged
provider-shaped `team.id` field as an implicit contract.

## 7. Identity Resolution Contract

For each event:

1. `event.team.id` is treated as a provider ID.
2. Resolve it using the existing Team Master identity lookup for
   `"api-football"`.
3. Use the returned `Team.team_id` as the Event's canonical Team identity.
4. Pass only that canonical value to `EventRepository`.

The provider ID must not be converted with a hard-coded table, inferred from
the number or name, or substituted with a Match ID. A missing or ambiguous
Team Master identity is not a valid canonical Event identity. The existing
repository raises on multiple matches; that error must remain explicit rather
than selecting an arbitrary Team.

## 8. Failure Contract

If a provider Team ID is present but Team Master cannot resolve it:

* Do not persist that event or any partial replacement snapshot.
* Do not fall back to the provider ID, the Match side ID, or a name-based guess.
* Report sync failure with reason `TEAM_IDENTITY_MISSING` and log the match and
  provider Team ID (without treating it as canonical).
* Fail the **entire Event snapshot**, not just the affected Event. Existing
  validation rejects the response as a whole before replacement, and sync
  currently builds the complete resolved list before calling
  `replace_match_events`. Skipping one event and continuing would change that
  all-or-nothing snapshot behavior.
* Do not commit or rollback inside `EventSyncService`. It flushes after a
  successful replacement but leaves transaction ownership to the caller.
  Existing callers roll back failed syncs (the admin route and scheduler);
  fixture finalization propagates Event failure into its existing enclosing
  failure/rollback path. Phase 3 must preserve those boundaries.

Team resolution should occur before repository replacement. If player/assist
resolution has already staged work in the same session before an unresolved
Team is encountered, the existing caller-owned rollback must discard that
work along with the failed operation.

## 9. Match/Event Consistency Contract

**Decision: REQUIRED for normal match Events.**

After Team Master resolution, the canonical Event Team ID must equal either
the canonical `matches.home_team_id` or `matches.away_team_id`. This is a
validation invariant only; Match fields are not an alternate identity resolver.
If it fails, fail the whole snapshot with a distinct explicit reason such as
`EVENT_TEAM_NOT_IN_MATCH`; never persist the mismatching identity.

The current provider validation requires each Event to contain a Team object
with an integer ID, and the existing Event types (`Goal`, `Card`, `subst`,
`var`, penalties, and other fixture Events) are represented as Events associated
with a fixture team. No exception for a non-participant Team is established by
the current repository contract. Phase 3 must test the named event categories
against this invariant. If future provider evidence establishes a legitimate
non-participant Event type, that type requires an explicit contract update; it
must not silently bypass validation.

## 10. Database Contract

The frozen meaning of `match_events.team_id` is **canonical
`teams.team_id`**.

Current ORM/schema facts:

* Type: integer.
* Nullability: non-nullable.
* Foreign key: none from `match_events.team_id` to `teams.team_id`.
* Index: none on `team_id` is declared by the current ORM/table creation;
  `match_id` has an index.
* Existing foreign keys: `match_id` references the Match row; player and assist
  IDs reference Player rows in the later migration. Team identity is not
  currently database-enforced.

The existing integer, non-null column is sufficient to hold the canonical ID
and no schema change is required to correct the Event sync mapping. The lack of
a Team foreign key means the database cannot enforce the semantic contract;
Phase 3 enforcement therefore belongs in EventSyncService. Adding a Team
foreign key or index is not part of this design freeze and would require a
separate schema decision/migration review.

## 11. API Contract

The Event API meaning of `team_id` is **canonical Fover Team ID**. The GET
events path reads the persisted value through EventService and must not map IDs
at response time.

The admin sync endpoint's `api_events` result is also in scope: Phase 3 must
ensure the returned representation does not present the raw provider
`event.team.id` as a canonical Event Team identity. No new provider identity
field is introduced by this freeze.

## 12. Provider ID Storage Decision

**No `provider_team_id` field is required for the current Event contract.**

The current Event response and consumers need the canonical Team identity;
provider Team identity is already resolvable through Team Master and is not
needed to read Events. Existing Event persistence separately retains provider
player and assist IDs, but that does not establish a requirement to retain a
provider Team ID. If a concrete consumer later requires it, add it only through
a separate schema/API design decision; do not overload `team_id`.

## 13. Cache / Lock / Transaction Compatibility

Identity correction is an internal data-contract correction. It must not add a
scheduler, queue, Redis identity registry, lock, transaction boundary, or
cache policy.

Preserve the existing behavior:

* Event operations use the per-match `"events"` resource lock at sync call
  sites (admin endpoint, scheduler, and fixture/finalization flows).
* Repository replacement deletes the current match snapshot and inserts the
  replacement; it does not append. The replacement remains within the caller's
  transaction.
* EventSyncService flushes but does not commit. Callers own commit/rollback.
* Admin sync and scheduler invalidate the Event cache after successful commit.
  Cache invalidation failure handling remains caller-owned.
* The scheduler continues its active-match discovery, refresh-window checks,
  terminal recovery, and existing retry/failure handling.
* Event reads remain DB/cache reads; they do not trigger a provider refresh.
* A Team identity failure prevents replacement and follows existing caller
  rollback behavior.

Relevant existing behavior is covered by
[`tests/test_events_lock_alignment.py`](../tests/test_events_lock_alignment.py)
and [`tests/test_event_service_freshness.py`](../tests/test_event_service_freshness.py).

## 14. Historical Data Policy

Phase 3 correctness applies to newly synchronized Event snapshots only.
Historical `match_events` rows are not rewritten, backfilled, deleted, or
otherwise repaired as part of this change. Existing rows containing provider
Team IDs require a separate read-only audit and separately approved repair
phase.

## 15. Phase 3 Implementation Requirements

Phase 3 must:

1. Add the existing Team Master lookup dependency to EventSyncService and
   resolve every provider `event.team.id` before replacement.
2. Load/use the Match's canonical home and away Team IDs for the required
   consistency check, without using them to resolve the provider identity.
3. Build an explicit canonical Event representation. Pass the canonical Team
   ID explicitly to EventRepository; the repository must not read the raw
   provider ID as its persistence source.
4. Return sync-result Events without exposing a provider Team ID as the
   canonical Team identity.
5. Fail the full snapshot with `TEAM_IDENTITY_MISSING` if Team Master lookup
   returns no Team, and with an explicit mismatch reason if the resolved
   canonical Team is not one of the Match sides.
6. Keep validation and identity resolution before replacement; preserve
   caller-owned rollback, locking, commit, and cache invalidation.
7. Leave database schema and historical Event rows unchanged.

### Expected Phase 3 files

Expected production changes are limited to:

* [`app/services/event_sync_service.py`](../app/services/event_sync_service.py)
  — Team resolution, membership validation, canonical Event representation,
  and explicit failure results.
* [`app/repositories/event_repository.py`](../app/repositories/event_repository.py)
  — consume and persist the explicit canonical Team ID only.

Expected test changes:

* Add [`tests/test_event_team_identity.py`](../tests/test_event_team_identity.py)
  — focused resolution, missing identity, membership, repository, and API
  contract tests.
* Extend
  [`tests/test_event_service_freshness.py`](../tests/test_event_service_freshness.py)
  — preserve whole-snapshot replacement, no-commit ownership, cache/read
  behavior, and no raw provider Team ID in API-shaped output.
* Extend
  [`tests/test_events_lock_alignment.py`](../tests/test_events_lock_alignment.py)
  only if needed to assert identity failures preserve existing lock and
  rollback behavior.

No schema migration is expected. `TeamRepository`, `TeamSyncService`,
`Match`/`Team` schemas, Event read serialization, API routing, scheduler lock
orchestration, and cache policy are not expected to change. If implementation
discovery shows a necessary deviation, stop for a separate design review
rather than silently expanding scope.

### Files that must not change for this correction

The following existing implementation surfaces are explicitly outside the
Phase 3 change set:

* [`app/providers/event_provider.py`](../app/providers/event_provider.py)
* [`app/repositories/team_repository.py`](../app/repositories/team_repository.py)
* [`app/services/team_sync_service.py`](../app/services/team_sync_service.py)
* [`app/services/event_service.py`](../app/services/event_service.py)
* [`app/api/matches.py`](../app/api/matches.py)
* [`app/models/match_event.py`](../app/models/match_event.py)
* [`app/models/match.py`](../app/models/match.py)
* [`app/models/team.py`](../app/models/team.py)
* [`app/schemas/match_event.py`](../app/schemas/match_event.py)
* Existing Alembic revisions, including
  [`alembic/versions/61d3320badab_create_match_events_table.py`](../alembic/versions/61d3320badab_create_match_events_table.py)
* [`app/services/scheduler.py`](../app/services/scheduler.py)
* [`app/services/fixture_sync_service.py`](../app/services/fixture_sync_service.py)
* [`app/services/cache_service.py`](../app/services/cache_service.py)

These boundaries preserve the existing provider transport, Team Master
identity mechanism, read/API serialization path, persistence schema, sync
orchestration, and cache behavior. Any implementation need to touch one of
these files must be raised as a design deviation before proceeding.

## 16. Test Contract

Phase 3 must provide the following measurable tests:

1. **Provider ID classification:** a provider Event's `team.id` is treated as
   an external identity and is not used directly for persistence or canonical
   API output.
2. **Successful Team Master resolution:** the existing lookup receives
   provider `"api-football"` and provider ID; its returned `Team.team_id` is
   selected as canonical.
3. **Canonical repository input:** EventRepository receives the canonical ID;
   the provider ID is not persisted as `match_events.team_id`.
4. **Canonical API output:** the read/API representation returns the
   persisted canonical `team_id`, and the sync endpoint does not label or
   expose provider identity as canonical.
5. **Unresolved provider identity:** no replacement occurs, sync returns
   `TEAM_IDENTITY_MISSING`, and caller transaction rollback behavior is
   preserved.
6. **Home-side Event:** resolved Event ID equals `match.home_team_id`.
7. **Away-side Event:** resolved Event ID equals `match.away_team_id`.
8. **Match membership failure:** a resolved canonical Team not in either
   Match side fails the entire snapshot before replacement.
9. **Event categories:** exercise normal events, substitutions, goals, cards,
   VAR, penalties, and other supported provider event types under the same
   home-or-away membership invariant.
10. **Replacement/idempotency:** repeated sync replaces the prior snapshot
    rather than appending or duplicating Events.
11. **Cache invalidation:** successful committed sync invalidates the same
    per-match Event cache key; failed sync does not publish a partial snapshot
    or alter caller-owned cache behavior.
12. **Lock and transaction regression:** existing admin, scheduler, and
    fixture/finalization lock and transaction behavior remains unchanged.
13. **Existing Event sync regression:** current validation, provider fixture
    ID selection, player/assist identity handling, and read-only Event GET
    behavior continue to pass.

## 17. Non-Goals

Phase 2 and this freeze do not include:

* production code implementation;
* database migration or schema modification;
* historical Event data repair;
* API redesign;
* a new Team Master architecture or resolver;
* a new scheduler, queue, or Redis identity registry;
* a new Event table;
* API-Football provider changes; or
* Flutter changes.

## 18. Final Design Decision

The Team Master is the sole canonical authority. EventSyncService resolves
provider Team identity through the existing Team Master mechanism and passes
canonical Team IDs to persistence. EventRepository persists canonical IDs
only. Event reads and API responses return canonical IDs only. Unresolved
provider identity is a whole-snapshot failure and is never persisted as a
canonical ID. Historical repair remains a separate phase.

The existing `match_events.team_id` column can hold this contract without a
schema change; its missing Team foreign key is documented but is not required
to implement the Phase 3 identity correction.

## 19. Design Freeze Status

**FROZEN — Phase 2 PASS.**

The identity source, lookup method, ownership boundaries, whole-snapshot
failure behavior, Match membership invariant, database semantics, API meaning,
and Phase 3 scope are explicit. The current implementation differs from this
target because it persists and can echo the provider `event.team.id`; Phase 2
has documented that gap and made no production-code or database changes.
