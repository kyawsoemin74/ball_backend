# BACKEND AUTH ARCHITECTURE — READ-ONLY AUDIT

## 1. Executive Summary

- The implemented backend auth API is centered on `POST /api/auth/google` and uses a JWT-based access/refresh token pair. The route is defined in `app/api/auth.py` and the token logic lives in `app/services/token.py` and `app/core/security.py` (SOURCE VERIFIED).
- The backend supports username/password auth through `/api/auth/login` and `/api/auth/register`, but these are standard FastAPI forms and CRUD endpoints rather than the current Flutter product flow. The current product requirement is Google Login as the primary auth path (SOURCE VERIFIED).
- Google authentication is performed by verifying a Google-issued ID token using `google.oauth2.id_token.verify_oauth2_token(..., settings.GOOGLE_CLIENT_ID)` and then extracting `email`, `sub`, and optional `name`/`picture` from the payload (SOURCE VERIFIED).
- The canonical Google identity used for user resolution is `users.google_id`, which is populated from Google `sub`. The system also checks `users.email` as a secondary linkage before creating a new user. `users.google_id` is unique in the schema and migration (SOURCE VERIFIED).
- Access tokens are JWTs with `type="access"` and expire after `ACCESS_TOKEN_EXPIRE_MINUTES` (30 minutes). Refresh tokens are JWTs with `type="refresh"` and expire after `REFRESH_TOKEN_EXPIRE_MINUTES` (10080 minutes / 7 days) (SOURCE VERIFIED).
- The refresh endpoint does generate a new token pair (`create_token_pair`) on every successful refresh, but it does not store refresh tokens server-side, does not blacklist the old token, and does not validate token history beyond JWT signature and type. This is stateless refresh issuance rather than secure rotation (SOURCE VERIFIED).
- 401 means the backend rejected the supplied credential or token due to invalid/missing/expired JWT or wrong username/password. 403 means the user is inactive or lacks admin role; it is not a logout state (SOURCE VERIFIED).
- No user-facing logout endpoint or token revocation implementation exists in the auth API. An admin-only session logout exists in `app/admin_auth.py`, which is for the SQLAdmin panel and not for API token invalidation (SOURCE VERIFIED).
- There is no dedicated auth repository layer; the auth service reads and writes the `users` table directly through the SQLAlchemy async session (SOURCE VERIFIED).

## 2. Auth Endpoint Inventory

| Endpoint | HTTP Method | Route File | Handler Function | Request Schema | Response Schema | Status |
|---|---|---|---|---|---|---|
| `/api/auth/register` | POST | `app/api/auth.py` | `register_user` | `UserCreate` (`username`, `email`, `password`) | `UserRead` | Backend available, not required for Flutter product |
| `/api/auth/login` | POST | `app/api/auth.py` | `login` | `OAuth2PasswordRequestForm` (`username`, `password`) | `Token` | Backend available, not required for Flutter product |
| `/api/auth/refresh` | POST | `app/api/auth.py` | `refresh_token` | Query param `refresh_token: str` (no schema model) | `Token` | Required for token refresh flow |
| `/api/auth/google` | POST | `app/api/auth.py` | `google_login` | `GoogleLoginRequest` (`token_in: str`) | `GoogleAuthResponse` | Current product-required path |

Evidence classification: SOURCE VERIFIED.

## 3. Google Login Contract

### Route and handler

- Route: `@router.post("/google", response_model=GoogleAuthResponse)` in `app/api/auth.py` (SOURCE VERIFIED).
- Handler: `google_login(request: GoogleLoginRequest, db: AsyncSession = Depends(get_db))` (SOURCE VERIFIED).
- Request schema: `GoogleLoginRequest` from `app/schemas/auth.py` is a Pydantic model with one field: `token_in: str` (SOURCE VERIFIED).
- Validation: `token_in` is required because it is non-optional and the Pydantic model defines it without a default (SOURCE VERIFIED).
- Meaning: the value is expected to be the Google-issued ID token sent by the client. The code calls `id_token.verify_oauth2_token(token_in, google_requests.Request(), settings.GOOGLE_CLIENT_ID)` (SOURCE VERIFIED).

### Google verification behavior

- Accepted credential: Google OAuth2 ID Token, not an access token or custom backend token.
- Verification library: `google.oauth2.id_token.verify_oauth2_token` from `google-auth` and `google.auth.transport.requests.Request()` (SOURCE VERIFIED).
- Identity extraction: `email = idinfo['email']`; `google_id = idinfo['sub']`; `name = idinfo.get('name', email.split('@')[0])`; `picture = idinfo.get('picture')` (SOURCE VERIFIED).
- Invalid Google auth handling: if verification raises `ValueError`, the backend raises `HTTPException(status_code=401, detail="Invalid Google token")` (SOURCE VERIFIED).

### Google response

- Returned body is `GoogleAuthResponse` (`app/schemas/token.py`) with fields:
  - `access_token: str`
  - `refresh_token: str`
  - `token_type: str = "bearer"`
  - `user: GoogleAuthUser`
- `GoogleAuthUser` fields:
  - `id: str`
  - `email: EmailStr`
  - `name: str | None`
  - `avatarUrl: str | None`
  - `provider: str = "google"`

Evidence classification: SOURCE VERIFIED.

## 4. User Identity / Creation Flow

### Identity resolution logic

`authenticate_google_user` does the following in `app/services/auth.py` (SOURCE VERIFIED):

1. Query `User.google_id == google_id`.
2. If not found, query `User.email == email`.
3. If an existing user is found by email, assign `user.google_id = google_id`.
4. If neither exists, generate a new user with:
   - `username = local-part of email`, then unique suffix if needed
   - `email = email`
   - `google_id = google_id`
   - `hashed_password = bcrypt hash of a random token_urlsafe(32)`
   - `role = "user"`
   - `is_active = True`
   - `display_name = username`
   - `avatar_url = picture`
   - `avatar_source = AvatarSource.GOOGLE` when picture exists, else `DEFAULT`

### Canonical Google identity

- Canonical Google identity is the `Google subject` (`sub`) stored in `users.google_id`.
- Email is used to match an already-existing local user, but not as the canonical auth identity for the Google provider. The backend still enforces `email` uniqueness via a unique constraint on `users.email` (SOURCE VERIFIED).

### Duplicate handling

- Duplicate `google_id` is prevented by a unique DB constraint on `users.google_id`.
- Duplicate email is prevented by a unique DB constraint on `users.email`.
- Duplicate usernames are prevented by the unique constraint on `users.username`.
- The code attempts to generate a unique email-derived username by appending random lowercase letters/digits if a duplicate is found (SOURCE VERIFIED).

Evidence classification: SOURCE VERIFIED.

## 5. Access Token Contract

- Token type: JWT.
- Issued by `TokenService._create_token` in `app/services/token.py`.
- Claims: `sub`, `role`, `type`, `exp`.
- Subject: the user’s `username`, passed as `subject=user.username` in `create_access_token`.
- Type: `"access"`.
- Expiration: `ACCESS_TOKEN_EXPIRE_MINUTES = 30` in `app/core/config.py`.
- Algorithm: `JWT_ALGORITHM` default `HS256`.
- Secret: not exposed here; the code uses `settings.JWT_SECRET_KEY` and does not print or expose it in responses. The secret is not reported here (SOURCE VERIFIED).

The JWT decoder validates the token and enforces `payload.get("type") == expected_type` before accepting it. If `type` mismatches, the response is 401 with `detail="Could not validate credentials"` and `WWW-Authenticate: Bearer` headers (SOURCE VERIFIED).

## 6. Refresh Token Contract

- Token type: JWT.
- Issued by `TokenService.create_refresh_token(subject, role)`.
- Claims: `sub`, `role`, `type`, `exp`.
- Subject: username.
- Expiration: `REFRESH_TOKEN_EXPIRE_MINUTES = 10080` (7 days) in `app/core/config.py`.
- Storage: not stored server-side; the system issues a JWT and expects the client to keep it. No refresh-token table or model was found in the auth domain (SOURCE VERIFIED).
- Rotation: no server-side rotation tracking. The refresh endpoint issues a fresh token pair by calling `auth_service.create_token_pair(user)` and returning both tokens, but it does not persist the old token or invalidate it. The old refresh token remains mathematically valid until expiry because there is no blacklist or DB record to revoke it (SOURCE VERIFIED).
- Revocation: not implemented in the auth API.

## 7. Refresh Rotation

- Successful `/api/auth/refresh` runs: `decode_token(refresh_token, expected_type="refresh")` -> `username = payload["sub"]` -> database lookup by `User.username` -> if found and active -> `return auth_service.create_token_pair(user)` (SOURCE VERIFIED).
- This means the backend responds with a newly generated access token and a newly generated refresh token.
- However, there is no refresh token persistence, no refresh token hash, no refresh blacklist, and no server-side record of previous refresh tokens. Therefore the backend does not have real, secure refresh-token rotation in the usual sense; it is stateless JWT reissuance (SOURCE VERIFIED).
- Conclusion: refresh rotation is implemented as reissue, not persistence-based invalidation. Old refresh JWTs are not invalidated in the backend (SOURCE VERIFIED).

## 8. Refresh Failure Matrix

| Condition | HTTP Status | Error Code | Error Response |
|---|---:|---|---|
| Missing refresh token | 422 or 403 depending on request parsing; source does not define a custom error for this specific case | N/A | Not explicitly handled in code; FastAPI will reject missing required parameter before route logic if it is a required query parameter |
| Malformed token | 401 | `Could not validate credentials` | `HTTPException(401, detail="Could not validate credentials", headers={"WWW-Authenticate":"Bearer"})` |
| Invalid token | 401 | `Could not validate credentials` | Same as above |
| Expired token | 401 | `Could not validate credentials` | Same as above, triggered by JWT decode failure |
| Revoked token | UNKNOWN | UNKNOWN | No revocation mechanism, no blacklist, no token table |
| Unknown user | 401 | `Could not validate refresh token` | `HTTPException(401, detail="Could not validate refresh token")` |

Evidence classification: SOURCE VERIFIED for known cases; UNKNOWN for revoked-token behavior and missing-token custom response.

## 9. 401 / 403 Contract

### 401

`401 Unauthorized` is raised when:

- token is missing, malformed, expired, invalid, or wrong type in `TokenService.decode_token` (SOURCE VERIFIED)
- `get_current_user` cannot locate the user or the user is inactive (SOURCE VERIFIED)
- username/password auth fails in `authenticate_user` (SOURCE VERIFIED)
- Google ID token verification fails in `/api/auth/google` (SOURCE VERIFIED)

Common message: `Could not validate credentials` or `Invalid Google token`.

### 403

`403 Forbidden` is raised when:

- `get_current_active_user` sees `current_user.is_active == False` -> `detail="Inactive user"` (SOURCE VERIFIED)
- `get_current_active_admin` sees `current_user.role != "admin"` -> `detail="The user does not have sufficient privileges"` (SOURCE VERIFIED)

This is not a logout state; it is a role/inactivity gate.

## 10. Login Endpoint

- Route: `/api/auth/login`
- Method: `POST`
- Request: `OAuth2PasswordRequestForm` (FastAPI form, not JSON)
- Fields: `username` and `password`
- Validation: required by FastAPI form parsing; `username` can be the username or email because `authenticate_user` uses `or_(User.username == username, User.email == username)`
- Password verification: `bcrypt.checkpw` against `user.hashed_password`
- Token response: `Token` with `access_token`, `refresh_token`, `token_type`
- Access token lifetime: 30 minutes
- Refresh token lifetime: 10080 minutes
- Errors: 401 for invalid username/password
- Classification: BACKEND AVAILABLE BUT NOT USED BY FLUTTER (SOURCE VERIFIED)

## 11. Register Endpoint

- Route: `/api/auth/register`
- Method: `POST`
- Request: JSON body matching `UserCreate`
- Fields:
  - `username: str` (`min_length=3`, `max_length=50`)
  - `email: EmailStr`
  - `password: str` (`min_length=8`)
  - `role: str` defaults to `"user"` in schema
- Duplicate handling: rejects if username or email already exists with `400 Bad Request` and detail `A user with that username or email already exists.` (SOURCE VERIFIED)
- User creation: `User(username, email, hashed_password, role)` and commit to DB
- Password handling: `self.hash_password(user_in.password)` with bcrypt
- Token response: none; the route returns `UserRead` after creation
- Errors: 400 on duplicate values; DB commit exceptions would surface differently depending on the platform
- Classification: BACKEND AVAILABLE BUT NOT USED BY FLUTTER (SOURCE VERIFIED)

## 12. Logout Status

- API logout endpoint: NOT IMPLEMENTED.
- Refresh token revocation: NOT IMPLEMENTED.
- Server-side session invalidation: NOT IMPLEMENTED for user auth tokens.
- Token blacklist: NOT IMPLEMENTED.
- SQLAdmin-specific logout exists in `app/admin_auth.py`: `request.session.clear()` and `return True` (SOURCE VERIFIED), but this is not the public auth API and does not revoke user JWTs.

Product decision: not required by current product scope. Source evidence shows no `/api/auth/logout` route or token invalidation mechanism in the auth API.

## 13. Database / Session Model

The actual auth persistence is the `users` table, represented by `app/models/user.py` (SOURCE VERIFIED).

Columns and attributes include:

- `id` (primary key)
- `username` (nullable: false)
- `email` (nullable: false)
- `hashed_password` (nullable: false)
- `google_id` (unique, nullable: true)
- `role` (nullable: false, default `user`)
- `is_active` (nullable: false, default true)
- `display_name`
- `avatar_url`
- `avatar_source` (`default`, `google`, `upload`)
- `created_at`
- `updated_at`

No `refresh_tokens`, `sessions`, `provider_identities`, `revoked_tokens`, or `token_blacklist` tables were found in the auth domain. The `SessionMiddleware` in `app/main.py` is for SQLAdmin / session-based admin UI behavior and not for API user token storage. Refresh tokens are stateless JWTs, not stored in DB (SOURCE VERIFIED).

## 14. Backend Auth Architecture

The actual auth architecture is:

- Route layer: `app/api/auth.py`
- Service layer: `app/services/auth.py`
- Token service: `app/services/token.py`
- Security dependencies: `app/core/security.py`
- Schema layer: `app/schemas/auth.py`, `app/schemas/token.py`, `app/schemas/user.py`
- Model layer: `app/models/user.py`
- Database layer: `AsyncSession` with SQLAlchemy queries against `users`

There is no dedicated `AuthRepository` class in this implementation. Direct database access happens in the auth service using SQLAlchemy queries on `User` (SOURCE VERIFIED).

## 15. Backend-Only Business Logic

The following rules are Backend responsibilities and MUST NOT be duplicated inside Flutter:

- Google credential verification using Google’s OAuth2 ID token validation (SOURCE VERIFIED)
- User identity resolution by Google `sub` and fallback email matching (SOURCE VERIFIED)
- User creation and provisioning logic, including password generation for Google-authenticated accounts (SOURCE VERIFIED)
- Duplicate identity handling across username, email, and Google user identity (SOURCE VERIFIED)
- Password validation using bcrypt (SOURCE VERIFIED)
- JWT creation and validation (SOURCE VERIFIED)
- Token expiration enforcement (SOURCE VERIFIED)
- Access/refresh token type checks (`"access"` vs `"refresh"`) (SOURCE VERIFIED)
- Refresh-token validation and reissue logic (SOURCE VERIFIED)
- Active-user checks and `is_active` gating (SOURCE VERIFIED)
- Admin authorization checks and role enforcement (SOURCE VERIFIED)
- Database persistence of the actual user record (SOURCE VERIFIED)

These rules are Backend responsibilities and MUST NOT be duplicated inside Flutter.

## 16. Flutter Contract Requirements

Minimum contract required for Flutter based on backend evidence:

- Google Login request: JSON body with a single required field `token_in` containing a Google ID token.
- Google Login response: JSON object with `access_token`, `refresh_token`, `token_type`, and nested `user` body containing `id`, `email`, `name`, `avatarUrl`, and `provider`.
- Refresh request: POST to `/api/auth/refresh` with required query parameter `refresh_token` containing a valid refresh JWT.
- Refresh response: JSON object with `access_token`, `refresh_token`, and `token_type`.
- 401: invalid/malformed/expired/missing refresh token or invalid credentials; also Google token validation failure yields 401.
- 403: inactive user or insufficient privileges; not a logout event.
- No logout endpoint is available in the backend auth API. Client-side cleanup is a product decision and not a backend contract requirement.

## 17. Final API Contract Matrix

| Endpoint | Request | Response | Auth Required | Token Result | Status |
|---|---|---|---|---|---|
| `/api/auth/google` | `{"token_in":"<Google ID token>"}` | `{"access_token":"...","refresh_token":"...","token_type":"bearer","user":{"id":"...","email":"...","name":"...","avatarUrl":"...","provider":"google"}}` | No | Issues access + refresh JWTs | Current product-required |
| `/api/auth/refresh` | Query param `refresh_token=<refresh JWT>` | `{"access_token":"...","refresh_token":"...","token_type":"bearer"}` | No | Issues new access + refresh JWTs | Required for token refresh |
| `/api/auth/login` | `username` + `password` form fields | `{"access_token":"...","refresh_token":"...","token_type":"bearer"}` | No | Issues access + refresh JWTs | Backend available, not used by Flutter |
| `/api/auth/register` | `{"username":"...","email":"...","password":"..."}` | User record | No | No token issuance | Backend available, not used by Flutter |
| Logout | No backend API logout route found | None | N/A | No token invalidation | Not implemented |

## 18. Confirmed Facts

- `/api/auth/google` verifies a Google ID token and issues JWTs after user resolution or creation (SOURCE VERIFIED).
- `/api/auth/refresh` validates a refresh JWT and issues a new access/refresh pair (SOURCE VERIFIED).
- Access token lifetime is 30 minutes (SOURCE VERIFIED).
- Refresh token lifetime is 10080 minutes (7 days) (SOURCE VERIFIED).
- `users.google_id` is the canonical Google provider identity used in the auth logic (SOURCE VERIFIED).
- No refresh-token table, blacklist, or logout route exists in the auth API (SOURCE VERIFIED).
- 401 and 403 are defined and their triggers are visible in the security code (SOURCE VERIFIED).

## 19. Unknowns

- Whether a production deployment uses a custom reverse proxy or client architecture that accepts the refresh token in a cookie or header instead of as a query parameter. The code explicitly expects it as a query parameter in the route signature, so the backend contract is known from source, but deployment details outside the source are not audited here (UNKNOWN).
- Whether the front-end intentionally does not use the `/api/auth/login` and `/api/auth/register` endpoints because of product policy. The code exists, but the product decision is external to the backend source (INFERRED/UNKNOWN).
- Whether the Google OAuth client ID is configured in a specific environment beyond `settings.GOOGLE_CLIENT_ID`; the source confirms it is read from settings but not the deployment value (UNKNOWN).

## 20. Risks / Architecture Gaps

- Stateless refresh JWTs are not invalidated on use. This is a security weakness in standard refresh-token rotation patterns (SOURCE VERIFIED).
- There is no server-side revocation or session store for refresh tokens, which prevents explicit logout or forced invalidation of a compromised refresh token (SOURCE VERIFIED).
- The `/api/auth/refresh` route accepts a refresh token as a query parameter, which is less safe than a standard Authorization header or request body contract for sensitive tokens (SOURCE VERIFIED).
- The backend in `app/admin_auth.py` uses a session cookie for SQLAdmin, but that is not the same as API JWT invalidation and should not be conflated with the public auth API contract (SOURCE VERIFIED).

## 21. Evidence Sources

- `app/api/auth.py` — route definitions, handlers, request validation, and endpoint behavior (SOURCE VERIFIED)
- `app/services/auth.py` — user resolution, Google creation flow, password hashing, token response generation (SOURCE VERIFIED)
- `app/services/token.py` — JWT creation, type validation, decode logic, expiration enforcement (SOURCE VERIFIED)
- `app/core/security.py` — `401`/`403` auth dependency semantics and role checks (SOURCE VERIFIED)
- `app/core/config.py` — expiry settings and Google client config (SOURCE VERIFIED)
- `app/models/user.py` — user schema, Google identity field, unique constraints, and auth profile fields (SOURCE VERIFIED)
- `app/schemas/auth.py` — Google request model (SOURCE VERIFIED)
- `app/schemas/token.py` — token and Google response models (SOURCE VERIFIED)
- `app/schemas/user.py` — username/password registration contract (SOURCE VERIFIED)
- `app/admin_auth.py` — SQLAdmin session logout implementation, not user-auth logout (SOURCE VERIFIED)
- `alembic/versions/20260828_add_google_id_to_users.py` — migration adding `google_id` unique field to `users` (SOURCE VERIFIED)
