# API-Football News Endpoint Audit

## Scope
Read-only audit of the backend configuration and integration used by Fover. No live API requests were made, no credentials were exposed, and no environment or code files were modified.

## 1) Actual API host used by Fover

Verified fact:
- The application settings define the provider base URL as `https://v3.football.api-sports.io` in `app/core/config.py`.
- The shared HTTP client sends the API key in the `x-apisports-key` header in `app/services/base/football_client.py`.
- The backend is therefore configured for the API-Sports v3 product, not the older `apifootball.com` host pattern.

Evidence:
- `app/core/config.py`: `FOOTBALL_API_BASE_URL: str = "https://v3.football.api-sports.io"`
- `app/services/base/football_client.py`: `self.headers = { "x-apisports-key": self.api_key, "Content-Type": "application/json" }`

Important note:
- The `.env` file in the active IDE selection was not read as a source of runtime values for this report; the environment file was not modified, and no secret values were printed. The repository example file shows the same variable names, and the application settings class is the relevant code-level source of truth.

## 2) Existing API client and config file paths

Verified facts:
- Config source: `app/core/config.py`
- Client implementation: `app/services/base/football_client.py`
- Provider abstraction: `app/providers/base/football_api_client.py`
- Local news route: `app/api/news.py`
- Local news database CRUD: `app/crud/news.py`

Relevant configuration:
- `FOOTBALL_API_BASE_URL`
- `FOOTBALL_API_KEY`

Relevant auth behavior:
- `FootballAPIClient.__init__()` sets `self.base_url = settings.FOOTBALL_API_BASE_URL`
- `self.headers` includes `x-apisports-key`
- The client validates `FOOTBALL_API_KEY` before requests and raises if missing

This means the backend is using a single API-Sports v3 client and the authentication method is API key in the `x-apisports-key` header.

## 3) Authentication method and environment variables

Verified fact:
- The app uses the environment variable `FOOTBALL_API_KEY` and the request header `x-apisports-key`.
- This is the API-Sports pattern used by the v3 API.

Evidence:
- `app/core/config.py`: `FOOTBALL_API_KEY: str`
- `app/services/base/football_client.py`: `"x-apisports-key": self.api_key`
- `.env.example`: `FOOTBALL_API_KEY=your_api_football_key`

No secret values are included in this report.

## 4) Existing API request services

Verified fact:
- The main request layer wraps the provider with a shared `FootballAPIClient` used throughout the product.
- This client is created and reused by the football service stack in `app/services/football.py`.

Evidence:
- `app/services/football.py` creates provider clients and uses `FixtureProvider`, `LeagueProvider`, `StandingProvider`, `StatisticsProvider`, `TeamProvider`, `OddsProvider`, `H2HProvider`, etc.
- `FootballAPIClient` is the central HTTP implementation used by those providers.

## 5) News API status in this codebase

Verified fact:
- The public backend exposes a local news API in `app/api/news.py`.
- The route names are `get_news`, `get_latest_news`, `get_transfer_news`, and `get_betting_tips`.
- The route does not call an upstream API provider at all; it queries the application's own database-backed `News` model via `app/crud/news.py`.

Evidence:
- `app/api/news.py` calls `news.get_news_by_category(...)` and `news.get_news_by_id(...)`
- `app/crud/news.py` uses SQLAlchemy `select(News)` against the local database

This is not an API-Football news integration. It is an internal news model endpoint.

## 6) Subscription verification method

Verified fact:
- The codebase does not make a `/status` request at all; the backend client implementation is not performing a subscription check against the provider endpoint.
- The client supports generic GET/POST requests but there is no explicit `/status` helper or subscription verification pathway in the code reviewed.
- Because no live API call was made in this audit, the live subscription state cannot be proven from runtime data.

How `/status` would verify a plan (in general):
- API-Sports v3 commonly exposes a `/status` endpoint that returns provider metadata such as account status, subscriptions, daily request quotas, and remaining calls.
- In a safe runtime check, one would confirm:
  - account active / subscription active status
  - plan type / product entitlements
  - current daily request limit
  - remaining requests for the day

This is a method for validating entitlement, but it is not something the current codebase does automatically.

Important note:
- The `v3.football.api-sports.io` product can verify subscription and quota via `/status` in principle.
- That does not mean the current backend is configured to invoke it or that a subscription has been validated in this environment.

## 7) Is `get_news` compatible with the current backend?

Verified fact from code:
- The current backend is configured for API-Sports v3 (`https://v3.football.api-sports.io`), not `apifootball.com`.
- There is no direct upstream `get_news` request in the codebase.
- There is no `apifootball.com` host or `APIkey` pattern configured in the app.

Conclusion:
- The backend does not currently have an `apifootball.com` Premium News integration path.
- The existing app can only use the configured API-Sports v3 client and its local database-backed news endpoints.

## 8) Does API-FOOTBALL PRO include `get_news`?

This is an external product question, not a runtime code fact.

Reasoned assessment:
- The application is configured for API-Sports v3, which is a distinct product from the older `apifootball.com` ecosystem.
- The local code and configuration show no direct integration to a separate `apifootball.com` Premium / News package.
- The backend cannot claim access to an `apifootball.com` `get_news` endpoint without a separate product configuration and subscription entitlements.

This is best treated as an external subscription/product distinction rather than a confirmed code-level capability.

## 9) Is a separate Premium subscription required?

Verified facts from the code:
- There is no apifootball.com client in the app.
- There is no separate premium-host configuration.

Assessment:
- If the requirement is access to an `apifootball.com` premium News endpoint, then yes: a separate subscription or provider configuration would be required.
- The current backend code does not indicate that this subscription is already configured or used.

This is a product-level conclusion based on architecture and product separation, not a live verification of account entitlements.

## 10) API credit implications

Verified fact:
- No live API request was executed during this audit.
- No provider credits were consumed.
- No external network request was made to the provider from this environment in the course of the audit.

Guidance:
- Calling `/status` or a news endpoint would consume credits only if the account and provider permit it.
- Because the app is not configured for the separate apifootball.com product, there is no evidence of credit use for `get_news` through the current backend setup.

## 11) Final status

Status: NOT VERIFIED

Why:
- The codebase clearly shows the active backend uses API-Sports v3 and not apifootball.com.
- The codebase clearly shows the app’s news endpoint is local/database-backed, not a direct upstream provider request.
- The live entitlement check for the actual subscription plan was not performed and no provider account verification was available in this environment.

This means:
- Code configuration audit: PASS
- Live product entitlement verification: NOT VERIFIED
- Compatibility claim for `apifootball.com` `get_news` via the current backend: FAIL for a direct claim of access

## 12) Recommended next step

Recommended next step for the user:
- Confirm the exact provider account and subscription product in the production environment.
- If the requirement is to use API-Sports v3, validate the active plan via `/status` from a properly authorized environment.
- If the requirement is specifically `apifootball.com` Premium News, add that provider configuration and separate credentials separately; do not assume the current API-Sports integration is sufficient.

## 13) Verified facts vs assumptions

Verified facts:
- The backend base URL is `https://v3.football.api-sports.io`.
- The API key is passed via the `x-apisports-key` header.
- The active app config uses `FOOTBALL_API_BASE_URL` and `FOOTBALL_API_KEY`.
- The local news endpoint is database-backed and not a direct upstream provider call.
- No live API request was made during this audit.

Assumptions / external product inferences:
- `apifootball.com` Premium News is a separate product from the API-Sports v3 integration.
- A separate subscription is required for the `apifootball.com` Premium News product.
- The live account state and exact plan entitlement were not directly verified from a provider response in this environment.

## Conclusion
The current Fover backend is configured for API-Sports v3, not the `apifootball.com` Premium News product. The local `get_news` endpoint is a database-backed news route, not an upstream provider integration. There is no evidence in the codebase that the current backend can call the separate `apifootball.com` `get_news` endpoint without a separate subscription and additional client configuration. Because live plan verification was not performed, the final operational status remains NOT VERIFIED.
