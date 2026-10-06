# PHASE 2.5 — AUTH LOGOUT IMPLEMENTATION CONTRACT FREEZE

## 1. Purpose

This document freezes the implementation contract for the future `POST /api/auth/logout` feature, without implementing it.

The frozen contract preserves the current backend architecture:

- Stateless JWT access token
- Stateless refresh token issued by the existing token service
- Existing refresh rotation remains in force
- No access-token blacklist
- Logout is current-session / current-device only
- Multi-device support is a future extension, not part of the initial scope

## 2. Frozen Baseline

The following requirements are already approved and may not change:

- Logout scope: CURRENT SESSION / CURRENT DEVICE ONLY
- Revocation target: SERVER-SIDE AUTH SESSION
- Access token blacklist: NOT USED
- Access token after logout: expires naturally
- Refresh token after logout: must no longer be usable
- Existing JWT architecture: remains
- Existing refresh rotation: remains
- Logout all devices: OUT OF INITIAL SCOPE
- Logout: IDEMPOTENT
- Redis: not the canonical session store

## 3. Decision #1 — Logout Authentication

Selected design: C. Access Token + session identity

Reasoning:

- The backend already authenticates requests through `get_current_user()` using `OAuth2PasswordBearer` and a bearer access token.
- This is the same user identity path already used on protected routes and is already compatible with the existing auth dependency chain.
- The logout implementation should therefore use the currently authenticated request to identify the user and then resolve the current auth session using a session identity (`sid`).
- This keeps logout tied to the current active identity without requiring a global user-wide or all-device revocation.
- It preserves the existing access-token contract and avoids introducing a new authentication mechanism that would conflict with the current `get_current_user` flow.

Final contract:

- `POST /api/auth/logout` requires a valid bearer access token.
- The token identifies the caller via the existing JWT `sub` claim.
- The backend resolves the current session via the session identity associated with that token.
- The refresh token is not accepted as the primary logout credential because the current architecture already uses the access token as the active user credential, while the refresh token remains the renewal credential.

Compatibility with existing architecture:

- `Authorization: Bearer <access_token>` already works with `OAuth2PasswordBearer`.
- `get_current_user()` already validates the token type as `access` and loads the user by `sub`.
- Session revocation is layered on top of this identity resolution rather than replacing it.

## 4. Decision #2 — Session Identifier (`sid`)

The final design is to introduce a globally unique session identifier called `sid`.

The exact contract is:

- `sid` is added to the access token
- `sid` is added to the refresh token
- the same `sid` is used by the auth session record
- `sid` is generated at session creation
- `sid` must be globally unique
- format: UUID v4 string (or equivalent globally unique opaque identifier)
- value is treated as an opaque identifier; it is not a user identifier and is not a secret

Canonical relationship:

Access Token -> sid
Refresh Token -> sid
Auth Session -> sid

Reasoning:

- It allows the backend to map an active user request to the exact current session.
- It makes current-session logout possible without affecting other sessions for the same user.
- It fits naturally with the existing architecture because the token already carries identity claims and can carry an additional session claim without replacing the current JWT model.

## 5. Decision #3 — Refresh Token Identity

Canonical storage approach: refresh token hash

The backend must not store the plaintext refresh token value.

The frozen storage rule is:

- store a one-way hash of the refresh token value, or a derived token identifier generated from the token in a non-reversible way
- never store plaintext refresh tokens in the auth session table
- the refresh token hash is bound to the same `sid` as the auth session

This must be a secure, opaque identity reference, not the raw token value.

Required contract:

- uniqueness: one refresh token identity per active auth session
- lookup mechanism: lookup by `sid` and/or refresh-token hash
- relationship to `sid`: each active auth session has exactly one current refresh token identity value
- relationship to refresh rotation: on rotation, the old refresh token identity becomes obsolete and the new refresh token hash is stored for the same `sid` or a new session record depending on the implementation choice

Recommended canonical pattern:

- `refresh_token_hash` in the session row
- value generated from a cryptographic hash of the raw refresh token payload or a stable token reference
- hash is never returned to the client

This preserves the current architecture while adding the minimum server-side state required for revocation.

## 6. Decision #4 — Auth Session Model

The conceptual auth session model is:

- `session_id` — primary key; opaque UUID or bigint surrogate key
- `sid` — unique, opaque session identifier
- `user_id` — user relationship
- `refresh_token_hash` — server-side identity for the active refresh token
- `expires_at` — session expiry timestamp
- `revoked_at` — nullable timestamp when session is revoked
- `created_at` — creation timestamp
- `updated_at` — optional but recommended for session lifecycle tracking

Frozen rules:

- Primary key: `session_id`
- User relationship: many sessions may belong to one user
- Unique constraints:
  - `sid` unique
  - `user_id + sid` is logically unique
  - `refresh_token_hash` unique for active refresh token values
- Active / revoked representation:
  - active session: `revoked_at IS NULL` and `expires_at > now()`
  - revoked session: `revoked_at IS NOT NULL`
- Expiration representation:
  - `expires_at` must be stored as an absolute timestamp
- Device metadata:
  - not required for the initial implementation
  - may be added later for multi-device analytics or future session listing, but not required for the initial contract

This model is conceptual only and is not implemented in this phase.

## 7. Decision #5 — Logout Request / Response

Endpoint:

`POST /api/auth/logout`

Authentication:

- authenticated user session
- the request is authenticated by bearer access token using the existing `get_current_user` dependency pattern

Request contract:

- request body: empty object or empty body
- required headers: `Authorization: Bearer <access_token>`
- no additional request payload required for the initial design

Success contract:

- HTTP status: `200 OK`
- response body: generic success payload without exposing session internals

Example:

```json
{
  "status": "success",
  "message": "Logged out"
}
```

Idempotent behavior:

- first logout: session revoked and success returned
- repeated logout: treat as success, no destructive error
- response must not leak whether the session existed before or whether it was already revoked

Reasoning:

- This avoids information leakage and supports safe idempotent behavior.
- It matches the expected lifecycle of a current-device logout without exposing internal session details.

## 8. Decision #6 — Expired Access Token

Selected behavior: A. Logout endpoint requires valid access token.

If the access token is expired, logout fails with the standard auth failure contract.

Final rule:

- expired access token = authentication failure for `/api/auth/logout`
- refresh token is not used as the primary credential for logout
- this preserves the current security model and prevents the logout endpoint from becoming an alternate way to bypass the existing access-token validation model

Security implications:

- prevents a stale access token from being silently accepted in a logout flow
- keeps enforcement aligned with the existing access-token semantics
- avoids forcing a broader revocation mechanism for expired access tokens

## 9. Decision #7 — Already Revoked Session

When the session is already revoked:

- treat as idempotent success
- do not return an error
- do not expose whether the session was previously revoked
- return a generic success response

Final contract:

- HTTP status: `200 OK`
- response: generic success payload

Reasoning:

- avoids leaking internal session state
- aligns with idempotent semantics
- safe for repeated logout requests from the same device or same client state

## 10. Decision #8 — Session Not Found

If the user is valid but the session cannot be found:

- do not expose session existence details to the caller
- return generic success for idempotent logout if this is treated as a safe cleanup path

Final contract:

- HTTP status: `200 OK`
- response body: generic success payload

Reasoning:

- avoids leaking whether a session existed or was revoked
- preserves the safe idempotent lifecycle of logout

## 11. Decision #9 — Refresh After Logout

Frozen behavior:

Logout
  ↓
Session REVOKED
  ↓
Refresh request fails

Validation order:

- refresh token JWT validation
- resolve `sid` from refresh token
- load auth session by `sid`
- if session is revoked or missing, reject the refresh

Expected error contract:

- HTTP status: `401 Unauthorized`
- error category: auth validation failure
- message: reuse existing refresh token validation contract; do not introduce a new refresh error family unless implementation requires it

Important constraint:

- this does not change the refresh implementation in the current codebase
- it only specifies that a revoked session must reject refresh after logout

## 12. Decision #10 — Multi-Device

The frozen auth session model supports multiple sessions per user.

Example:

- User A has Session A, Session B, Session C
- Session B logs out
- Session A remains active
- Session C remains active

This is sufficient for future support of logout-all-devices without introducing it in the initial scope.

The model is intentionally designed so that:

- each session is independent
- a revoke action affects only the selected `sid`
- future logout-all-devices can be implemented as a bulk session revoke by `user_id` without changing the current session model meaningfully

## 13. Decision #11 — Session Expiration / Cleanup

When `session.expires_at < now()`:

- the session is logically expired
- it is treated as inactive for refresh and logout actions
- cleanup may be handled in future maintenance work
- cleanup is not required for the initial logout implementation contract

Frozen behavior:

- expired sessions are not treated as active refresh sessions
- logout of an expired session is harmless and should be idempotent
- no cleanup enforcement is required in this contract

## 14. Decision #12 — Transaction Boundary

The frozen transaction boundary is:

API
  ↓
Auth Service
  ↓
Session revoke
  ↓
Commit

The transaction owner is:

- API route receives request
- Auth service resolves the current user and session
- session revocation logic updates the auth session row
- commit occurs in the database session boundary

Failure requirement:

- if the database update fails, the logout must not be reported as successful
- the system must treat database failure as a failed logout operation

This follows the existing auth architecture of service-level business logic and DB session ownership, without inventing a separate transaction architecture.

## 15. Decision #13 — Cache / Redis

The final contract states:

- Redis is not the canonical session store
- logout does not require Redis
- token blacklist is not introduced

If Redis is used in the future for optimization, it is future work only and is out of scope for this contract.

## 16. Decision #14 — Security Review

The frozen logout contract is designed to preserve, not weaken, existing security:

- session hijacking: reduced by binding refresh session revocation to a session identity rather than a user-wide state
- refresh token theft: session revocation prevents further refresh use after logout
- refresh token replay: stale or replayed refresh tokens will fail if the session is revoked
- old rotated refresh token: old rotated tokens remain invalid because refresh rotation logic remains unchanged and is checked against active session state
- revoked session: refresh will fail for that session
- expired session: treated as inactive and rejected
- repeated logout: idempotent success
- multiple devices: each device has its own `sid` and session state

This contract does not redesign refresh rotation and therefore does not weaken the current refresh-token model.

## 17. Final Implementation Contract Table

| Item | Final Decision | Status |
|------|----------------|--------|
| Logout Scope | Current-session / current-device only | FROZEN |
| Endpoint | `POST /api/auth/logout` | FROZEN |
| Authentication | Access token + session identity | FROZEN |
| Session ID | `sid` (globally unique opaque identifier) | FROZEN |
| Token Identity | Refresh token hash / non-plaintext identity reference | FROZEN |
| Session Storage | Server-side auth session row keyed by `sid` | FROZEN |
| Logout Response | `200 OK` with generic success payload | FROZEN |
| Idempotency | Repeat logout returns success without leaking state | FROZEN |
| Expired Access Token | Logout requires valid access token; expired access token fails auth | FROZEN |
| Already Revoked | Idempotent success; no state leak | FROZEN |
| Session Not Found | Generic success for idempotent logout | FROZEN |
| Refresh After Logout | Reject with auth failure; refresh token invalid for revoked session | FROZEN |
| Multi-device | Multi-session per user; each session independent | FROZEN |
| Logout All | Out of scope for initial implementation | OUT OF SCOPE |
| Redis | Not canonical session store; no blacklist | FROZEN |
| Transaction | API -> service -> session revoke -> commit | FROZEN |
| Access Token Blacklist | NO | FROZEN |

No unresolved implementation decisions remain in this contract.

## 18. Implementation Contract Flow

### Login

User Login
  ↓
Create Auth Session
  ↓
Generate `sid`
  ↓
Create access token and refresh token bound to `sid`
  ↓
Store refresh token hash / secure identity reference for the session
  ↓
Return token pair

### Refresh

Refresh Token
  ↓
Validate JWT
  ↓
Resolve `sid`
  ↓
Load Auth Session
  ↓
Session ACTIVE?
  ├── NO → Reject
  └── YES
        ↓
Existing Refresh Rotation
        ↓
Issue new token pair

### Logout

`POST /api/auth/logout`
  ↓
Authenticate current user via access token
  ↓
Resolve `sid`
  ↓
Load Auth Session
  ↓
Revoke Session
  ↓
Commit
  ↓
Success response

### After Logout

Refresh Token
  ↓
Session REVOKED
  ↓
Reject

Access Token
  ↓
Natural expiration

## 19. Final Decision

This contract is frozen and ready for the implementation phase.

PHASE 2.5 STATUS: IMPLEMENTATION CONTRACT FROZEN

Confirmed:

- Current-session logout is the approved scope.
- `sid` is the canonical session identifier.
- Server-side auth session revocation is the logout mechanism.
- Access-token blacklist is explicitly not used.
- Refresh token identity is stored only as a secure non-plaintext reference.
- The refresh token will be rejected after the session is revoked.
- Current token architecture and refresh rotation remain unchanged.

No implementation has been performed in this phase.
