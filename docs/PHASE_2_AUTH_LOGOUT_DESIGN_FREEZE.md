# PHASE 2 — AUTH LOGOUT DESIGN FREEZE

## 1. Design Scope

This document records the approved future logout architecture for the Fover Backend as a design-only freeze. It is intentionally not an implementation plan and does not modify any backend source, schema, migrations, tokens, or runtime behavior.

This phase is limited to architecture design for:

- current-device / current-session logout
- server-side revocation of the refresh session
- natural expiry of the access token
- future support for multi-device sessions without forcing that model into the current freeze

This design deliberately excludes:

- logout all devices
- access-token blacklist
- immediate access-token invalidation
- forced logout of all sessions
- any database or code implementation work

## 2. Frozen Target Architecture

### 2.1 Logout scope

Logout is defined as a current device / current session logout only.

Example:

- Device A logs out
- Device B remains authenticated

This freeze does not implement logout-all-devices.

### 2.2 Revocation target

The primary revocation target is the server-side authentication session, specifically the refresh session.

Conceptual flow:

Logout
  ↓
Auth Session = REVOKED
  ↓
Refresh Token cannot be used
  ↓
Access Token expires naturally

This is a deliberate design decision: logout does not require immediate access-token invalidation.

## 3. Current Backend State (Frozen Baseline)

The existing backend architecture remains the frozen baseline unless a later implementation phase proves otherwise:

- Stateless JWT-based authentication
- access token + refresh token
- login and Google Login issue the same token-pair model
- refresh token rotation already exists
- no server-side refresh-token storage currently exists
- no logout endpoint currently exists
- no token blacklist currently exists
- no session invalidation currently exists

The design freeze must preserve this contract and must not redesign the current token system.

## 4. Server-Side Auth Session Model (Conceptual Only)

The target future server-side concept is:

Auth Session
  - session_id
  - user_id
  - refresh-token identity
  - expires_at
  - revoked_at
  - created_at

This model is conceptual only and must not be created in this phase.

The exact storage representation of the refresh-token identity is intentionally left for implementation design.

## 5. Refresh Session and Token Identity

The design introduces a session identifier concept:

- sid

Target relationship:

Access Token
  └── sid

Refresh Token
  └── sid

Auth Session
  └── sid

Purpose:

- identify the current authentication session
- allow current-device/session logout
- avoid revoking unrelated sessions

This does not mean the current token generation is modified in this phase. The `sid` concept is only a future architectural anchor for logout design.

## 6. Access Token Behavior

The design intentionally does NOT introduce an access-token blacklist.

After logout:

- Access Token remains valid until natural expiration
- Refresh Session is revoked
- Refresh Token cannot be used for future refresh

This is an explicit security/design decision.

Implication:

- logout does not require immediate access-token invalidation
- the system remains compatible with the current stateless JWT access model
- enforcement of logout is focused on the refresh session

## 7. Refresh Token Behavior

Refresh Token must be associated with the same authentication session.

Target relationship:

Refresh Token
  ↓
  sid
  ↓
  Auth Session

When the session is revoked:

- refresh token cannot be used for future refresh
- the system prevents further token renewal from that session

The current refresh-token rotation architecture remains in scope and must not be redesigned during this phase.

## 8. Logout Endpoint (Conceptual Contract)

Target endpoint:

POST /api/auth/logout

Target authentication:

- authenticated user/session

The endpoint must identify the current authentication session without accidentally revoking all user sessions.

The exact request/response schema is not finalized in this design freeze, but the design requires:

- current-session logout
- no destructive effect on unrelated sessions
- no accidental global logout

The request must identify the caller’s active session and the backend must resolve the corresponding refresh session.

## 9. Idempotency Requirements

Logout must be idempotent.

Behavior:

First logout:

- session becomes REVOKED
- success

Second logout:

- session already REVOKED
- still safe
- no destructive error

The design should avoid exposing superfluous session-state details to the caller.

## 10. Expired Access Token Boundary

This phase does not define the final behavior for a logout request made with an expired access token.

This is intentionally left as a design boundary to resolve during implementation/API contract design.

The freeze only records that:

- expired access tokens are not the primary revocation signal
- the auth session state remains the authoritative logout target
- the final contract for expired-access-token logout is not frozen yet

## 11. Design Constraints

The following constraints are part of the approved freeze:

- Do not create the database table yet.
- Do not create an Alembic migration yet.
- Do not add a refresh-token blacklist yet.
- Do not make access-token invalidation immediate.
- Do not redesign the existing JWT refresh rotation behavior.
- Do not invent a new token storage model beyond the concept of a session identity (`sid`).
- Do not claim a final implementation contract for `sid` storage, hashing, or persistence representation.

## 12. Architecture Alignment with Existing Auth

This design intentionally aligns with the current backend architecture:

- access token remains stateless and decodable
- refresh token remains the session-bound renewal token
- logout is implemented as a server-side refresh-session revocation problem
- current session is identified by `sid` rather than by a global user-wide or device-wide state

This preserves the existing auth contract while creating the missing logout capability in a future implementation phase.

## 13. Open Design Decisions Remaining for Future Implementation

The following decisions remain outside this freeze and must be resolved in the next implementation/API contract design phase:

- Must logout require a valid access token?
- May logout accept a refresh token instead of or in addition to the access token?
- Does logout revoke only the current session or all sessions for the same user?
- How is the current session resolved when the access token is expired?
- How is the refresh token associated with the correct `sid` without stored plaintext refresh token values?
- What is the safe storage representation for refresh-token identity/reference?
- What is the exact request schema for `POST /api/auth/logout`?
- What is the exact response contract for success, already-revoked, and invalid session states?
- How does logout behave for a rotated refresh token?
- How should logout be treated when the access token is already expired but the refresh session is valid?

These are design gaps, not implementation instructions.

## 14. Security Boundary

The design clarifies the intended security boundary:

- Access Token: expires naturally; not immediately revoked
- Refresh Session: server-side revocation target
- Session identity: `sid` identifies current device/session
- Refresh-token use is denied once the session is revoked

This keeps logout aligned to the existing JWT architecture while introducing a server-side session concept for revocation.

## 15. Design Freeze Decision

This design is frozen as the approved target architecture for logout.

Target properties:

- current-device logout only
- server-side session revocation for refresh session
- access token remains valid until natural expiry
- refresh token becomes unusable once session is revoked
- no access-token blacklist in this design
- no multi-device logout in this freeze
- no implementation yet

## 16. Phase 2 Final Verdict

PHASE 2 STATUS: DESIGN FREEZE APPROVED

Confirmed:

- The future logout architecture is defined as current-session logout.
- Refresh-session revocation is the primary model.
- Access-token blacklisting is intentionally not part of the frozen design.
- The system must preserve the existing JWT + refresh rotation architecture.
- No code or schema changes were made during this phase.

Not confirmed / deferred:

- final request/response contract for `POST /api/auth/logout`
- exact storage format for refresh-token identity/reference
- expired access-token logout handling
- idempotent API semantics in final response payload
- multi-device support details

This phase is design only and is complete without implementation.
