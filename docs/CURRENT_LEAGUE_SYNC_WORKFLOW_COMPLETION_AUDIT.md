# CURRENT LEAGUE SYNC WORKFLOW — COMPLETION AUDIT

## 1. Executive Summary

The overall workflow is not complete.

The evidence shows that the system does not currently guarantee that an arbitrary new Provider League ID can enter the stack, resolve to a canonical League Master row, and then propagate correctly through Fixture Sync, Match, Standing, Odds, and H2H without League-specific logic or a special-case mapping.

This conclusion is not based on a single report or a single test case. It is based on the actual architecture, the live schema, the real runtime lookup path, and the known failure pattern observed in the live League 389 case and the broader league master data.

The core issue is structural and repeated in code and runtime behavior:

- The canonical League identity is treated as `(provider, provider_id)`.
- The local master identity is `league_id`.
- `League.provider_id` is nullable.
- `League.country_id` is nullable.
- `allowed_leagues` is only keyed by local `league_id`.
- Fixture Sync resolves provider payloads with an exact lookup on `(provider, provider_id)`.
- When no provider identity is present, the sync skips the fixture and can return HTTP 200 while writing zero records.

That means the workflow is not generic and not yet verified for new provider leagues.

---

## 2. Phase Status Table

| Phase | Status | Source Evidence | DB Evidence | Runtime Evidence | Missing Evidence |
|---|---|---|---|---|---|
| Phase 1 | PASS | Code and audit docs clearly identify `league_id`, `provider`, `provider_id`, `country_id`, and `allowed_leagues` as separate identity layers. | Real database audits show many null/missing provider and country identities. | Runtime logs confirm unresolved provider-identity skip behavior. | None for diagnosis; but not enough for operational completion. |
| Phase 2 | FAIL | Design documents define the intended canonical mapping, but the implementation does not enforce it. | `provider_id` remains nullable; many rows have no valid identity mapping. | Fixture sync still fails closed on unresolved provider identity. | No proof of full enforcement at DB or runtime boundary. |
| Phase 3 | FAIL | Onboarding flow is described generically but not proven generically. | No real generic new-league DB evidence showing creation + valid mapping + allowed state for an arbitrary provider league. | No production or runtime evidence that an arbitrary new league resolves and persists end-to-end. | Generic onboarding verification is missing. |
| Phase 4 | FAIL | Real DB validation is incomplete and not generic. | Live database evidence includes unresolved identities, null provider IDs, and invalid country links. | The observed real-case mismatch proves the generic flow is not safe. | Fresh DB verification for a new arbitrary provider league is absent. |
| Phase 5 | FAIL | Provider payload and local resolution path are well-understood, but runtime success does not imply sync success. | `League.provider_id` may be empty, so the provider-locallookup fails at the DB boundary. | Logs show: `Skipping fixture with unresolved provider League: provider_id=...` and zero writes. | Real end-to-end sync proof for arbitrary league is missing. |
| Phase 6 | FAIL | Repeat sync and idempotency are not proven generically. | No verified repeated sync evidence for a newly mapped arbitrary provider league. | No runtime proof of a second sync preserving League/Season/Team identities without duplicates. | Repeat/idempotency evidence is missing. |
| Phase 7 | FAIL | Downstream chain is known in architecture, but not fully verified for a new provider league. | No complete downstream DB persistence evidence across Match/Standing/Odds/H2H for a generic provider league. | No runtime evidence that all downstream modules accept the same resolved local `league_id` without league special cases. | Full downstream verification is missing. |
| Phase 8 | FAIL | Final freeze is not allowed because the previous phases are not closed with verified evidence. | No end-to-end DB evidence for a generic new league across the full pipeline. | No successful generic new-league runtime flow was proven. | Final freeze criteria remain unmet. |

---

## 3. Actual Current Position

COMPLETED PHASES:
- Phase 1: current identity diagnosis and current architecture understanding are complete.
- The code and live audits correctly document the problem: identity resolution depends on `(provider, provider_id)`, while local League identity is `league_id` and the allow-list is separate.

IN-PROGRESS PHASE:
- No verification phase is genuinely complete enough to merit a production freeze.

NOT VERIFIED:
- Phase 3: generic new-league onboarding
- Phase 4: real PostgreSQL verification for arbitrary new provider league
- Phase 5: real provider sync chain for arbitrary new provider league
- Phase 6: repeat sync / idempotency for arbitrary new provider league
- Phase 7: downstream verification across Match / Standing / Odds / H2H
- Phase 8: final freeze

BLOCKERS:
- `League.provider_id` is nullable and many local League rows are unresolved.
- The data model does not enforce a valid `(provider, provider_id)` mapping before fixture sync proceeds.
- `allowed_leagues` is not a substitute for provider identity.
- The runtime fixture path explicitly skips provider fixtures when the exact lookup `(api-football, provider_id)` resolves to no row.
- A route can return HTTP 200 while still persisting zero Matches and zero downstream rows.
- No generic end-to-end proof exists that a completely new Provider League ID reaches Match / Standing / Odds / H2H without hardcoded logic or special cases.

---

## 4. Generic New-League Verification

Question: Can a completely new Provider League ID enter the system and automatically reach Match / Standing / Odds / H2H correctly?

Answer from evidence: No.

Why:

1. The canonical mapping is not enforced before sync.
   - `app/models/league.py` defines `provider_id` as nullable.
   - `app/repositories/league_repository.py` only resolves a league when the exact `(provider, provider_id)` exists.
   - `app/services/fixture_sync_service.py` explicitly drops fixtures when that lookup fails.

2. The allow-list is not identity evidence.
   - `app/repositories/allowed_league_repository.py` simply returns local `league_id` values from `allowed_leagues`.
   - That table does not prove the provider identity exists or is valid.

3. Runtime success is not equivalent to persistence success.
   - The fixture sync path can return `success=True` while `inserted=0`, `updated=0`, and `total=0` when no provider identity resolves.

4. The real League 389 case demonstrates the generic failure mode.
   - The provider payload carried a valid provider league ID.
   - The local row existed and was allowed, but `provider_id` was NULL.
   - Lookup `(api-football, 389)` returned zero rows.
   - All fixtures were skipped.

Therefore, no evidence supports the statement that an arbitrary new Provider League can flow end-to-end through the system without manual repair or special handling.

---

## 5. League 389 Interpretation

League 389 is a test case and failure case, not the architectural target.

This is important: League 389 is valuable as evidence of the real failure mode, but it must not be treated as proof that the architecture is complete.

The correct interpretation is:

- The system is supposed to use canonical provider identity `(provider, provider_id)`.
- The League 389 case proves that the current implementation does not guarantee this mapping for an existing local League.
- The failure is not about League 389 being special; it is about a generic class of unresolved provider identities.
- The same failure pattern would apply to any newly introduced provider league whose local row is missing or has a null provider identity.

In other words, League 389 is evidence of a generic identity defect, not a valid pass case for the overall workflow.

---

## 6. Remaining Work

The remaining work required before Phase 8 Final Freeze is limited to actual verification gaps:

1. Verify that a new arbitrary provider league can be created or resolved with a valid `(provider, provider_id)` mapping before any sync operation proceeds.
2. Verify that `country_id` is resolved and linked correctly for a newly introduced league without hardcoded assumptions.
3. Verify that `allowed_leagues` is assigned only to a valid canonical League row, not to an unresolved local row.
4. Verify the full DB state for the new league: League Master row, provider identity, country linkage, season linkage, and duplicate prevention.
5. Verify the real provider fixture sync chain from provider payload to local `league_id` to Match persistence.
6. Verify repeat sync idempotency for the same provider league without duplicate Match/Season/League rows.
7. Verify downstream persistence and read-backs for Match, Standing, Odds, and H2H using the same resolved local `league_id`.
8. Verify the full generic workflow using a new provider league ID, not a previously tested league.

---

## 7. Final Classification

WORKFLOW NOT YET VERIFIED

---

## Evidence basis used for this audit

This audit relies on the actual implementation and runtime evidence in the repository, including:

- `app/models/league.py`
- `app/repositories/league_repository.py`
- `app/repositories/allowed_league_repository.py`
- `app/services/league_sync_service.py`
- `app/services/fixture_sync_service.py`
- `app/services/country_sync_service.py`
- `app/services/standing_sync_service.py`
- the live audit reports under `docs/` documenting the unresolved provider identity failure mode and the League 389 runtime behavior

This is a verification-only audit. No source code, database data, schema, migration, scheduler behavior, or configuration was changed during this review.
