# Phase 0 Research: Lead Capture & Conversion Backend Rewrite

Each item resolves a "NEEDS CLARIFICATION" from the initial Technical Context pass. Format: Decision / Rationale / Alternatives considered.

## 1. Web framework

**Decision**: Starlette, served by Uvicorn (ASGI) — no FastAPI.

**Rationale**: Per explicit project constraint, only Starlette and Uvicorn are used as the web framework/server layer (matches `pyproject.toml`, which already lists `starlette` and `uvicorn` with no FastAPI dependency). Starlette's own `Route`/`Router` gives HTTP routing, its `Request`/`JSONResponse` give body parsing and response construction, and its `starlette.background.BackgroundTask` gives the same deferred-persistence primitive FastAPI's `BackgroundTasks` would have wrapped (FR-007) — the response returns before the background write runs, and a failure there doesn't affect the response already sent. Native `async def` handlers still suit the I/O-bound nature of both endpoints (outbound calls to PayPal, SMTP, Postgres). Request validation is **not** hand-rolled: `pydantic` v2 models (invoked explicitly in each handler, rather than via FastAPI's automatic dependency-injected validation) continue to give a near-direct replacement for the existing Zod schemas (`validateServerContact`), including custom refinements (pickup-time-≥1hr, phone normalization).

**Alternatives considered**: FastAPI (rejected — explicit project constraint restricts the framework/server layer to Starlette + Uvicorn; FastAPI is itself built on Starlette, so this decision is a strict subset, not a divergence, and the automatic OpenAPI/dependency-injection machinery FastAPI adds isn't needed for two hand-written endpoints); Flask (mature, but sync-first — would need extra plumbing for background work and async outbound calls); Litestar (comparable feature set to FastAPI but a smaller ecosystem/less prior art for this kind of small service — no material advantage here).

## 2. Database access

**Decision**: `asyncpg` with hand-written parameterized SQL (no ORM), wrapped in a small connection-pool module.

**Rationale**: The current system already talks to Neon with raw, parameterized SQL via `@neondatabase/serverless` (`INSERT ... ON CONFLICT (txn) DO NOTHING`, a lookup by `detail.reference`). `asyncpg` is the fastest, most direct async Postgres driver for Python and lets the rewrite reuse the exact same statements/idempotency semantics. An ORM (SQLAlchemy) would add a mapping layer with no corresponding benefit for two tables and a handful of queries, and risks subtly changing the `ON CONFLICT DO NOTHING` write semantics FR-015 depends on.

**Alternatives considered**: SQLAlchemy (async) — more ceremony than needed; `psycopg3` async — equally valid technically, `asyncpg` chosen for being the more common default in the async-Python/ASGI ecosystem, no strong reason to prefer one over the other otherwise.

**Follow-up for implementation**: The rewrite must connect to the *same* live `quote_requests` and `conversions` tables (per the confirmed data-continuity decision). Exact column types/constraints should be introspected from the live schema (e.g., `information_schema.columns`) before writing migrations/DDL, rather than assumed from the feature description alone — see `data-model.md` for the inferred shape and its caveats.

## 3. Access control for the quote-submission endpoint (FR-001a)

**Decision**: An app-level shared-secret check (a bearer token or custom header the frontend attaches on every request, compared with a constant-time comparison) on the quote-submission route specifically — independent of network topology. Layer Fly private networking (6PN) underneath as defense-in-depth, with the frontend calling the backend via its internal `.internal` address rather than a public URL, if/when the frontend is also deployed on Fly.

**Rationale**: The PayPal webhook (FR-008) and the quote-submission endpoint (FR-001a) have opposite exposure requirements — PayPal must reach the webhook from the public internet, so the app as a whole cannot be private-network-only. Since both routes live behind the same public ingress, network-level isolation can't be the sole gate for the quote route; an explicit per-route check is required regardless of deployment topology, and continues to work even if the frontend is later moved off Fly or off the private network. Fly private networking is still valuable as a second layer when both services are co-located on Fly, but it is not sufficient on its own.

**Alternatives considered**: Mutual TLS (strong, but meaningfully more operational overhead — cert issuance/rotation — for a two-endpoint service with one internal caller; disproportionate to the risk); IP allowlisting (fragile against Fly's dynamic/ephemeral machine IPs, and doesn't help if the frontend isn't on Fly); relying on Fly private networking alone (rejected — doesn't work for a single app serving both a public and a frontend-only route, per the rationale above).

## 4. Operator notification for silent failures (FR-020, FR-021)

**Decision**: Send a short operator-alert email (reusing the same SMTP mechanism as the lead notification email, addressed to the business's operational inbox) for exactly the two conditions FR-020/FR-021 name: a webhook authenticity-verification failure, and a conversion entry recorded with no usable contact identifiers at all.

**Rationale**: SC-008 requires the operator not have to inspect raw logs to notice these conditions — a log line alone doesn't satisfy that. Email is already a fully built dependency of this system (FR-005), so this reuses existing infrastructure rather than introducing a new alerting provider (Slack/PagerDuty/Sentry) that wasn't part of the original system and isn't otherwise justified in scope. Structured logging (see #6) still records full detail for later debugging; the email is just the "someone needs to know now" signal, kept deliberately narrow per the "intentional, scoped" clarification (not one alert per failed request — see implementation-phase note below on avoiding alert storms).

**Alternatives considered**: Push to a third-party alerting/observability service — rejected as new, unjustified scope for a two-endpoint rewrite; log-only with reliance on the hosting platform's log viewer — rejected, doesn't meet SC-008's "without manually inspecting raw logs" bar.

**Follow-up for implementation**: because a compromised/misconfigured PayPal integration could cause many verification failures in a burst, the task-planning phase should consider basic de-duplication/rate-limiting of the alert email itself (e.g., at most one alert per condition per short window) so an incident doesn't itself become a mail-flood; this is an implementation-tasks concern, not a spec-level one.

## 5. PayPal webhook signature verification

**Decision**: Verify manually using the `cryptography` library (RSA PKCS#1v1.5 signature verification with SHA-256) against the fetched certificate, plus `zlib.crc32` for the body checksum — mirroring the current `id|time|webhookId|crc32(body)` construction exactly. Certificate fetched via `httpx` with a short timeout, restricted to `https://` URLs on `*.paypal.com`.

**Rationale**: This is a direct, dependency-light port of the existing verification logic (FR-008) with no behavior change, and keeps the trust boundary (domain + scheme restriction on the cert URL) explicit and auditable in one place rather than hidden inside an SDK.

**Alternatives considered**: An official/community PayPal Python SDK — evaluated and not preferred, since webhook signature verification is a small, self-contained piece of crypto logic and pulling in a general-purpose SDK for it adds dependency surface without simplifying this specific requirement; the SDK question can be revisited separately for outbound invoice creation (see #7).

## 6. Observability approach (FR-022)

**Decision**: Standard library `logging`, configured for structured (JSON) output, with log statements placed deliberately at the failure points FR-022 names (webhook verification failure, invoice-creation timeout/failure, lead-email failure, quote/conversion persistence failure) — not at every request/response.

**Rationale**: Matches the explicit "intentional and scoped, not blanket/exhaustive" clarification. Structured logs are consumable by Fly's log pipeline without adding a new logging dependency; JSON output keeps failure context (which step, which identifiers-safe-to-log, error detail) machine-parseable if a log-based alert is added later.

**Alternatives considered**: `structlog` — nicer ergonomics but an extra dependency for a service this size; stdlib `logging` with a JSON formatter gets the same practical outcome with fewer moving parts.

## 7. PayPal invoice draft creation & OAuth token caching

**Decision**: `httpx.AsyncClient` for OAuth2 client-credentials token fetch and the `/v2/invoicing/invoices` draft-create call; cache the access token (and its expiry) as process-lifetime module state.

**Rationale**: Since the rewrite runs as a long-lived server process (not a serverless function), "cache across warm invocations" from the original simply becomes "cache for the life of the process, refresh on expiry" — a strictly simpler version of the same idea, needing no external cache (Redis, etc.).

**Alternatives considered**: A PayPal SDK — rejected for the same reason as #5; the surface area used (token fetch + one POST) is small enough that a thin `httpx` wrapper is clearer and easier to bound to the required 5-second timeout (FR-004/SC-002) than adapting an SDK's own retry/timeout behavior.

## 8. Phone number parsing & validation

**Decision**: `phonenumbers` (the official Python port of Google's libphonenumber).

**Rationale**: Direct parity with the current system's use of `libphonenumber-js` for E.164 normalization (contact validation), PayPal's `{country_code, national_number}` shape, and free-text US phone extraction from invoice content.

**Alternatives considered**: None seriously — this is the same library family the current system already depends on, just the Python port; no reason to diverge.

## 9. Email rendering (HTML + plain text)

**Decision**: `Jinja2` templates for the lead-notification email (and the operator-alert email from #4), rendered to both an HTML and a plain-text version; sent via `smtplib`/`aiosmtplib` over the same SMTP provider (Gmail by default, per current config).

**Rationale**: React-Email's job was rendering a component to HTML + text; Jinja2 is the direct, dependency-light equivalent for server-rendered email in Python. No client-side interactivity is needed for an email.

**Alternatives considered**: Plain Python string templates (f-strings) — workable but harder to keep the HTML and text versions visually organized and testable as the template grows; Jinja2's small overhead is worth it for maintainability.

## 10. Configuration & secrets

**Decision**: `pydantic-settings` for typed, validated environment-variable configuration (PayPal credentials/webhook IDs, SMTP config, DB URL, the frontend shared secret from #3, `PHONE_DISPLAY`/`PHONE_TEL`/`COBALT_EMAIL`, `GOOGLE_ADS_ID`/`GA4_ID`).

**Rationale**: Gives the same "fail fast on missing/malformed config" property Zod-validated env access implicitly gave the Next.js app, in one typed place, matching FR-017's requirement that shared config (service/vehicle option lists) not diverge across use sites — the same module also holds the plain constants.

**Alternatives considered**: Bare `os.environ` access scattered across modules — rejected, loses the single-source-of-truth property and typed validation.

## 11. Testing strategy

**Decision**: `pytest` + `pytest-asyncio`, with Starlette's `TestClient` (`starlette.testclient.TestClient`, backed by `httpx`) for endpoint-level (contract) tests, plus focused unit tests for the pure-logic modules (identifier hashing, invoice-phone extraction, click-id staleness gate, webhook signature verification, validation refinements).

**Rationale**: Standard, well-supported combination for an async Starlette service; lets contract tests exercise the two endpoints exactly as specified in `contracts/`, and lets the higher-risk pure-logic pieces (hashing/staleness/signature math) be tested in isolation without spinning up the full app or a real database.

**Alternatives considered**: None material — this is the de facto standard for this stack.
