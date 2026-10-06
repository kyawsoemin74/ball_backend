# AUTH LOGOUT SOURCE AUDIT

## 1. Audit Scope

This document audits the existing backend authentication architecture as implemented in source code, without modifying any auth logic, DB schema, migration, or runtime state. The focus is on the exact behavior of the login, refresh, Google login, current-user authentication, token lifecycle, and the integration points that would be required for a later logout feature.

The primary source files reviewed are:

- `app/api/auth.py`
- `app/services/auth.py`
- `app/services/token.py`
- `app/core/security.py`
- `app/api/deps.py`
- `app/schemas/auth.py`
- `app/schemas/token.py`
- `app/schemas/user.py`
- `app/models/user.py`
- `app/core/config.py`
- `app/main.py`
- `app/admin_auth.py`

No auth logic was changed in this phase.

## 2. Existing Auth Architecture

The implemented architecture is a stateless JWT-auth system layered as follows:

Client
  ↓
API route (`app/api/auth.py`)
  ↓
Auth service (`app/services/auth.py`)
  ↓
Token service (`app/services/token.py`)
  ↓
User model + database session (`app/models/user.py`, SQLAlchemy async session)
  ↓
Response

Important source facts:

- There is no auth repository layer. `auth_service` directly queries the `users` table using the SQLAlchemy async session (`select(User)...`).
- There is no refresh-token table, token table, revoked-token table, or session table in the auth domain.
- There is no server-side refresh-token state. The refresh token is a JWT payload and is not persisted server-side.
- There is no API logout route or revocation endpoint in the public auth router.
- The `get_current_user` dependency uses `OAuth2PasswordBearer` to validate the bearer access token and then loads the user by username from the DB.

## 3. Login Flow

### 3.1 Login endpoint

- Route: `POST /api/auth/login`
- File: `app/api/auth.py`
- Handler: `login()`
- Request: `OAuth2PasswordRequestForm = Depends()`
- Form fields: `username`, `password`

### 3.2 Authentication method

The route calls:

`user = await auth_service.authenticate_user(db=db, username=form_data.username, password=form_data.password)`

Then returns:

`auth_service.create_token_pair(user)`

### 3.3 User lookup and verification

In `app/services/auth.py`:

- `authenticate_user()` performs:
  - `select(User).where(or_(User.username == username, User.email == username))`
- It loads the matching user.
- It validates the supplied password with:
  - `bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))`
- If no user matches or the password is wrong, it raises:
  - `HTTPException(401, detail="Incorrect username or password", headers={"WWW-Authenticate": "Bearer"})`

### 3.4 Token generation

In `AuthService.create_token_pair(user)`:

- `access_token`: `self.token_service.create_access_token(user.username, user.role)`
- `refresh_token`: `self.token_service.create_refresh_token(user.username, user.role)`
- `token_type`: `"bearer"`

### 3.5 Token payload and expiration

`TokenService._create_token()` creates a JWT with standard claims:

- `sub`: subject, from username
- `role`: user role
- `type`: `"access"` or `"refresh"`
- `exp`: expiration timestamp

In `app/core/config.py`:

- `ACCESS_TOKEN_EXPIRE_MINUTES = 30`
- `REFRESH_TOKEN_EXPIRE_MINUTES = 10080` (7 days)
- `JWT_ALGORITHM = "HS256"`

### 3.6 Token storage

There is no server-side login/session persistence for the issued JWTs. The backend does not write the access or refresh token to a database table or Redis key for auth state.

### 3.7 Response schema

`Token` (`app/schemas/token.py`):

- `access_token: str`
- `refresh_token: str`
- `token_type: str = "bearer"`

### 3.8 Transaction ownership

- Login does not commit any DB transaction on successful auth.
- The route itself does not issue a commit; it merely validates credentials and returns a token pair.
- No user session record is created.

### 3.9 Flow summary

Client
  ↓
`POST /api/auth/login`
  ↓
`authenticate_user()`
  ↓
SQLAlchemy user lookup by username/email
  ↓
`bcrypt.checkpw()`
  ↓
`create_token_pair()`
  ↓
JWT access + refresh issuance
  ↓
`Token` response

## 4. Refresh Token Flow

### 4.1 Route and request

- Route: `POST /api/auth/refresh`
- File: `app/api/auth.py`
- Handler: `refresh_token(refresh_token: str, db: AsyncSession = Depends(get_db))`

This route receives a refresh token through a required function parameter, not through a model schema. The function signature is effectively a query parameter contract because FastAPI binds a required parameter to the request URL.

### 4.2 Validation

The route calls:

`payload = auth_service.token_service.decode_token(refresh_token, expected_type="refresh")`

This invokes `TokenService.decode_token()` in `app/services/token.py`.

Behavior:

- `jwt.decode(token, secret_key, algorithms=[algorithm])`
- It then checks `payload.get("type") == expected_type`
- It also checks `payload.get("sub")` and `payload.get("role")`
- If invalid/malformed/expired/wrong type, it raises:
  - `HTTPException(401, detail="Could not validate credentials", headers={"WWW-Authenticate": "Bearer"})`

### 4.3 User validation

After decode, the route extracts:

- `username = payload["sub"]`

Then it queries:

`select(User).where(User.username == username)`

If the user is missing or inactive, it raises:

- `HTTPException(401, detail="Could not validate refresh token")`

### 4.4 Refresh token storage

There is no persistence for refresh tokens in the backend. The refresh token is not stored in a table, session record, Redis key, or token database. Source evidence shows no refresh-token model or table.

### 4.5 Rotation behavior

The route returns:

`return auth_service.create_token_pair(user)`

This creates a brand new access and refresh JWT pair for the user, but it does not record the old refresh token, compare it to any prior value, or invalidate it.

This means the backend does not implement secure refresh-token rotation with persistent token state. It issues a new pair statelessly.

### 4.6 Flow summary

Client
  ↓
`POST /api/auth/refresh`
  ↓
`decode_token(..., expected_type="refresh")`
  ↓
JWT signature + type validation
  ↓
`User.username == payload["sub"]`
  ↓
active user validation
  ↓
`create_token_pair(user)`
  ↓
new access + refresh JWTs
  ↓
`Token` response

## 5. Refresh Token Storage

Status: DOES NOT EXIST.

Strong evidence from source:

- No refresh-token table or model in the auth domain.
- No `refresh_tokens`, `sessions`, `revoked_tokens`, or token state models found in the repository.
- `TokenService` generates JWTs and never writes them to storage.
- `AuthService` never stores the refresh token in DB or Redis.
- `SessionMiddleware` exists in `app/main.py` for SQLAdmin session usage, but it is not used for API refresh-token storage.

## 6. Refresh Rotation

The source shows a real implementation pattern, but it is not secure persistent rotation.

Actual behavior:

- Old refresh token is accepted if it is valid and of type `refresh`.
- New access token is issued.
- New refresh token is issued.
- Old token is not tracked, blocked, or invalidated.

So the flow is:

Old Refresh Token
  ↓
JWT validated
  ↓
User validated
  ↓
New access token + new refresh token issued
  ↓
Old refresh token remains valid until expiry unless it naturally expires

This is not server-side rotation; it is stateless reissue. There is no `jti`, `token_id`, `token_version`, `session_id`, `revoked_at`, `replaced_by`, or equivalent in the auth code path.

Search result summary for token identity constructs:

- `jti`: not found in auth source
- `token_id`: not found
- `session_id`: not found in the auth implementation
- `refresh_token_id`: not found
- `token_version`: not found
- `revoked_at`: not found
- `replaced_by`: not found
- `expires_at`: not found as a token-state row model

## 7. Access Token Architecture

### 7.1 Type

- JWT
- Issued by `TokenService.create_access_token(subject, role)`

### 7.2 Lifetime

- `ACCESS_TOKEN_EXPIRE_MINUTES = 30`
- Defined in `app/core/config.py`

### 7.3 Claims

- `sub`
- `role`
- `type`
- `exp`

### 7.4 Storage

- Not stored server-side
- Not persisted in DB or Redis

### 7.5 Revocation

There is no server-side blacklist or revocation store. A token can be invalidated only by natural expiration or by the client not using it anymore. The system does not have immediate token invalidation logic.

### 7.6 Immediate invalidation capability

Answer from source evidence: NO.

The current backend cannot immediately invalidate an already-issued access token because:

- JWTs are stateless
- there is no token blacklist
- there is no token revocation store
- the access token is not persisted in any auth session state

## 8. Current User Resolution

### 8.1 Authentication dependency chain

`app/core/security.py` defines:

- `oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")`
- `get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db))`

Flow:

Bearer Token
  ↓
OAuth2PasswordBearer extracts token from `Authorization` header
  ↓
`token_service.decode_token(token, expected_type="access")`
  ↓
JWT validation for `type == "access"`
  ↓
`sub` is read as username
  ↓
`select(User).where(User.username == username)`
  ↓
user retrieved from DB
  ↓
if not user or user not active -> 401
  ↓
returns the user object

### 8.2 Active-user check

`get_current_active_user()`:

- checks `current_user.is_active`
- if false: `HTTPException(403, detail="Inactive user")`

### 8.3 Admin check

`get_current_active_admin()`:

- depends on `get_current_active_user()`
- checks `current_user.role != "admin"`
- if so: `HTTPException(403, detail="The user does not have sufficient privileges")`

### 8.4 Meaning of auth identity

User identity is determined by the JWT `sub` value, which is the `User.username`. The database lookup then resolves to the corresponding `users` row.

## 9. Google Login

### 9.1 Route and request

- Route: `POST /api/auth/google`
- File: `app/api/auth.py`
- Handler: `google_login(request: GoogleLoginRequest, db: AsyncSession = Depends(get_db))`
- Request schema: `GoogleLoginRequest` in `app/schemas/auth.py`

`GoogleLoginRequest` contains exactly one field:

- `token_in: str`

This is required and is expected to be a Google ID token.

### 9.2 Verification flow

The route verifies the Google token with:

- `id_token.verify_oauth2_token(token_in, google_requests.Request(), settings.GOOGLE_CLIENT_ID)`

This uses Google’s OAuth2 ID token verification library.

### 9.3 Extracted data

From the verified payload, the code extracts:

- `email = idinfo['email']`
- `google_id = idinfo['sub']`
- `name = idinfo.get('name', email.split('@')[0])`
- `picture = idinfo.get('picture')`

### 9.4 User lookup and creation

`authenticate_google_user()` in `app/services/auth.py` does the following:

1. Query `User.google_id == google_id`
2. If not found, query `User.email == email`
3. If an existing user is found by email, set `user.google_id = google_id`
4. If neither match, create a new user:
   - unique email-derived username
   - random bcrypt password
   - `google_id` set
   - `role="user"`
   - `is_active=True`
   - `display_name=username`
   - `avatar_url=picture`
   - `avatar_source=GOOGLE` if picture exists, else `DEFAULT`

### 9.5 Same token system

Google Login uses the same backend JWT pair system:

- `auth_service.create_token_pair(user)`
- access token + refresh token
- same `TokenService`
- same `Token` response structure

This means Google Login is not a separate session system; it is the same authentication model, just user-created or resolved from Google claims.

### 9.6 Logout relationship

Logout would still need to operate against the same JWT and refresh-token model because Google Login returns the same token pair and uses the same access/refresh architecture.

## 10. Existing Revocation / Blacklist Mechanisms

Search results show no implemented public revocation or blackout mechanism in the auth codebase.

Relevant search hits:

- `app/admin_auth.py` contains a session-based logout for SQLAdmin (`request.session.clear()`) but this is not the public auth API and does not revoke user JWTs.
- No `logout` endpoint exists under `app/api/auth.py`.
- No `revoke`, `blacklist`, or `token blacklist` code path was found in the auth implementation.
- No token table or revoked-token store exists.

Classification:

- `logout` route: NOT IMPLEMENTED
- `revoke_token`: NOT IMPLEMENTED
- `revoked_tokens`: NOT IMPLEMENTED
- `token blacklist`: NOT IMPLEMENTED
- `session invalidation`: ONLY SQLAdmin session clear, not user auth token invalidation
- `redis revocation`: NOT FOUND

## 11. Database / Redis Auth State

### 11.1 Database state

The auth database state is effectively only the `users` table.

`app/models/user.py` contains:

- `id`
- `username`
- `email`
- `hashed_password`
- `google_id`
- `role`
- `is_active`
- `display_name`
- `avatar_url`
- `avatar_source`
- `created_at`
- `updated_at`

No auth state tables were found for:

- refresh tokens
- sessions
- token versions
- revoked tokens
- provider tokens
- login history

### 11.2 Redis state

Redis is used elsewhere in the project for cache and operational features, but no auth token state or revocation mechanism was found in the reviewed source. No auth-key naming pattern for token state, blacklist, or session invalidation was identified.

Status: EXISTS for app caching, but not for auth token state. Auth token state: DOES NOT EXIST.

## 12. Transaction Ownership

### 12.1 Register flow

`POST /api/auth/register` calls `auth_service.register_user(...)`.

`register_user()`:

- checks for duplicate `username` or `email`
- creates a new `User`
- `db.add(user)`
- `await db.commit()`
- `await db.refresh(user)`

### 12.2 Google login flow

`auth_service.authenticate_google_user(...)`:

- may update existing user
- may create a new user
- `db.add(user)` if new
- `await db.commit()`
- `await db.refresh(user)`

### 12.3 Login flow

No commit is performed. The flow is read-only credential validation followed by token generation.

### 12.4 Refresh flow

No DB write occurs on successful refresh. It only validates a JWT and issues a new JWT pair.

### 12.5 Ownership summary

- API route: request entry point and response shaping
- Auth service: credential validation, user creation, user linking, token creation orchestration
- Database session: actual SQLAlchemy state and commit/rollback operations
- Repository: none for auth; direct model queries occur in service layer

## 13. Logout Integration Point

The source does not currently define any logout integration point in the public auth API. The logical integration points would later be in one of these places:

- `app/api/auth.py` for the public route
- `app/services/auth.py` for auth lifecycle logic
- `app/services/token.py` for token/state invalidation logic
- perhaps a persistent revocation store, if designed later

However, the existing source does not provide any such integration or supporting persistence model. In the current implementation, the only logout-like behavior is the SQLAdmin session clear in `app/admin_auth.py`, which is independent from API JWT auth.

## 14. Security Boundary

For a future `POST /api/auth/logout`, the current architecture allows some paths to be reasoned about but not implemented with source evidence:

- A valid Access Token identifies the user via `sub` and `Authorization` header.
- A valid Refresh Token identifies the user via `sub` and `type == "refresh"`.
- The current system does not have a refresh session ID or token store.
- If the Access Token is expired, it will fail JWT decode and return 401.
- If the Refresh Token is already expired, it will fail validation and return 401.
- If the Refresh Token has already been rotated, the backend has no stored record of prior refresh tokens, so it cannot detect it.
- If logout is called twice, the current architecture has no idempotent revocation mechanism to track prior invalidation.

The current architecture supports token validation, but not token revocation semantics.

## 15. Logout Capability Assessment

### 15.1 What existing authentication state can actually be revoked?

Answer from source evidence: NONE.

- No token blacklist
- No revoked token table
- No refresh token store
- No session store for user auth

### 15.2 What existing token/session identifier can identify the refresh session?

Answer: NONE.

The refresh token is a JWT with `sub`, `role`, `type`, and `exp`, but there is no server-side refresh session record or token ID.

### 15.3 Can Logout revoke the current refresh token/session safely?

Not with the current architecture. There is no persisted refresh session to revoke.

### 15.4 Can the current access token be immediately revoked?

No. It is stateless and not tracked.

### 15.5 What would a future logout require?

Likely one or more of the following, but none is implemented in the current source:

- new DB table for refresh sessions or revoked tokens
- new token claim such as `jti` or `session_id`
- new session identifier
- new revocation/blacklist logic in token validation
- new API contract for logout semantics

## 16. Design Gaps

The following are unresolved design decisions for a future logout feature:

- Must logout require a valid Access Token?
- Must logout accept a Refresh Token?
- Should logout accept both?
- Should logout revoke only the current device session or all devices?
- Should access token invalidation be immediate or only advisory?
- Should expired access tokens be allowed to trigger logout cleanup?
- Should a revoked refresh token be rejected as 401 or explicitly as a logout status?
- Should logout be idempotent?
- Is there a requirement for multi-device session tracking?
- Is a new backend table required to persist refresh sessions?
- Is Redis required for revocation state?

All of the above remain design decisions, not current source-backed behavior.

## 17. Frozen Architecture Compatibility

The current auth system is compatible with the following patterns:

- Access token validation through `Authorization: Bearer <token>`
- User lookup by username from JWT `sub`
- Stateless JWT issuance for access and refresh tokens
- Google Login and password login sharing the same token issuance model

However, adding logout directly into the current architecture would conflict with the existing design because:

- there is no token/session store
- there is no server-side refresh token record
- there is no revocation mechanism
- token validation is stateless
- there is no logout contract in the API layer

This means any logout feature would be a new server-side stateful layer added on top of the current stateless model, not an extension of the current back-end behavior without further design.

## 18. Source Evidence

- `app/api/auth.py` — login, register, refresh, Google routes and handlers
- `app/services/auth.py` — password verification, Google user lookup/creation, token pair creation
- `app/services/token.py` — JWT generation and validation logic
- `app/core/security.py` — access token validation and authorization checks
- `app/core/config.py` — token expiry and JWT algorithm configuration
- `app/models/user.py` — user table, `google_id`, and auth-related fields
- `app/schemas/auth.py` — Google request schema
- `app/schemas/token.py` — access/refresh response schema
- `app/schemas/user.py` — login/register user contract
- `app/admin_auth.py` — SQLAdmin session logout implementation, unrelated to public auth API
- `app/main.py` — router registration and session middleware

All findings in this document are based on source review only.

## 19. Phase 1 Verdict

### Required verification summary

- Source files changed: 0 (backend Python source files)
- Database migrations created: 0
- Database data changed: 0
- Runtime authentication behavior changed: NO
- Logout endpoint added: NO

### Final status

PHASE 1 STATUS: PASS

What is confirmed:

- The backend’s auth model is stateless JWT-based access/refresh issuance.
- Login, refresh, and Google login all follow the same token pair pattern.
- Refresh tokens are not stored in a persistent backend state.
- No logout endpoint or revocation model exists in the public auth API.
- Current-user authentication is derived from the bearer access token and a DB lookup on `User.username`.
- 401 and 403 are implemented in security dependencies and token validation logic.

What is not confirmed:

- Any future logout contract beyond the current stateless JWT architecture.
- Any required session store or revocation layer that may be added in a later design phase.
- Any multi-device or all-device logout semantics.

What design decisions remain:

- Whether logout uses access token, refresh token, or both.
- Whether server-side refresh-session tracking will be introduced.
- Whether logout is current-device-only or all-device.
- How immediate access-token invalidation will be handled without a blacklist or token store.
- Whether Google-authenticated sessions are treated exactly the same as username/password sessions.

Whether PHASE 2 Design Freeze is safe to start:

- Safe to start only as a formal design freeze exercise, provided the remaining logout decisions are explicitly captured before implementation. The current source evidence confirms the existing auth architecture but does not provide a ready-made logout implementation or persistence layer.

## Required Table

| Area | Current Implementation | Evidence | Logout Relevance |
|------|------------------------|----------|------------------|
| Login | Username/email + bcrypt auth; issues JWT pair | `app/services/auth.py`, `app/api/auth.py` | Existing login path does not include logout state |
| Access Token | JWT with `sub`, `role`, `type`, `exp`; 30 minutes | `app/services/token.py`, `app/core/config.py` | Stateless; cannot be revoked without new state |
| Refresh Token | JWT with `sub`, `role`, `type`, `exp`; 7 days | `app/services/token.py`, `app/core/config.py` | No persistence or revocation model |
| Refresh Storage | Does not exist | `app/services/auth.py`, `app/services/token.py` | Critical missing requirement for logout |
| Refresh Rotation | Stateless reissue only; no invalidation record | `app/api/auth.py` | Old refresh token is not revoked |
| User Identity | `User.username` + user lookup from JWT `sub` | `app/core/security.py`, `app/models/user.py` | Current user is identified by username from access token |
| Google Login | Google ID token verification and linked/recreated user record | `app/api/auth.py`, `app/services/auth.py` | Uses same JWT flow as regular auth |
| Session State | No backend auth session table or refresh session | `app/models/user.py`, code review | No logout revocation state exists |
| Revocation | Not implemented | source search across auth modules | Logout cannot be supported without new state |
| Redis | Exists for project cache, not auth token state | `app/main.py`, app cache code | No token blacklist or session state in Redis |
| Transaction | API/service DB commit points exist for create/link user; refresh/login do not write | `app/services/auth.py` | Future logout must define transaction/peristence design |
| Logout | Not implemented | `app/api/auth.py`, route scan | No public logout contract exists |

## Final status line

PHASE 1 STATUS: PASS
