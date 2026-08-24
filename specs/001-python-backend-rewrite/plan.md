# Implementation Plan: Lead Capture & Conversion Backend Rewrite

**Branch**: `001-python-backend-rewrite` | **Date**: 2026-08-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-python-backend-rewrite/spec.md`

## Summary

Port the two server-side flows of the existing Next.js backend (quote/contact submission → draft invoice + lead email + persistence; PayPal "invoice paid" webhook → verified, idempotent Google Ads conversion write) to a standalone Python HTTP service, preserving outward behavior (SC-007) while closing an access-control gap the rewrite itself introduces (FR-001a: the quote endpoint becomes a directly-callable HTTP endpoint instead of a same-origin Server Action) and adding narrowly-scoped operator alerting for two failure conditions that previously failed silently (FR-020/FR-021). The rewritten service reads and writes the *same* live `quote_requests`/`conversions` tables as the current system, which keeps running unchanged until a clean, one-time cutover.

## Technical Context

**Language/Version**: Python 3.14 (per `.python-version` / `pyproject.toml`)

**Primary Dependencies**: Starlette + Uvicorn (ASGI web service, no FastAPI — per explicit project constraint, matches `pyproject.toml`); `asyncpg` (Postgres/Neon access, raw parameterized SQL); `httpx` (outbound calls to PayPal's OAuth/invoicing API and cert fetch); `cryptography` (RSA/SHA-256 webhook signature verification); `phonenumbers` (E.164 parsing/validation, invoice-text phone extraction); `Jinja2` (lead-notification + operator-alert email rendering, HTML + text); `pydantic` v2 (request validation, invoked explicitly per handler rather than via FastAPI's dependency injection) + `pydantic-settings` (typed env config)

**Storage**: PostgreSQL (Neon) — same `quote_requests` and `conversions` tables the current system uses (see `data-model.md`); no new tables

**Testing**: `pytest` + `pytest-asyncio`; Starlette's `TestClient` (`starlette.testclient.TestClient`) for endpoint-level contract tests; focused unit tests for hashing, invoice-phone extraction, click-id staleness, and webhook signature verification

**Target Platform**: Linux server (containerized), deployed on Fly.io; the quote-submission route is restricted to the authorized frontend via an app-level shared secret (FR-001a; see `research.md` #3), the webhook route remains publicly reachable (PayPal must reach it from the internet)

**Project Type**: Single-project web service (backend API only) — the frontend is a separate, unchanged application that calls into this service; not a "frontend + backend" monorepo layout since no frontend code lives in this repository

**Performance Goals**: No explicit throughput target (small-business traffic volume); the one hard latency constraint is SC-002 — invoice creation must never delay the submission response by more than 5 seconds

**Constraints**: Webhook endpoint must always return `200` promptly regardless of downstream outcome (FR-016); quote-submission response must not block on deferred persistence (FR-007); operator alerts must fire for exactly two named conditions, not broadly (FR-020–FR-022)

**Scale/Scope**: Two HTTP endpoints plus supporting library modules; leads on the order of tens per day, not a high-throughput system — this is a like-for-like rewrite of an existing small service, not new capacity planning

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` contains only the unfilled placeholder template — no project-specific principles, sections, or governance rules have been ratified for this repository. There are no gates to evaluate against. This gate is treated as **passing (vacuously)**; if a constitution is authored later (`/speckit-constitution`), this plan should be re-checked against it before implementation proceeds further.

*Post-Phase-1 re-check*: unchanged — no constitution to re-evaluate against.

## Project Structure

### Documentation (this feature)

```text
specs/001-python-backend-rewrite/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md         # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   ├── quote-submission.md
│   └── paypal-webhook.md
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/cobalt_backend/
├── __init__.py            # exposes main() for the `cobalt-backend` script entry point
├── main.py                # Starlette app construction + Uvicorn boot
├── config.py               # pydantic-settings: DB URL, PayPal creds/webhook ids, SMTP,
│                           #   FRONTEND_SHARED_SECRET, PHONE_DISPLAY/PHONE_TEL, COBALT_EMAIL,
│                           #   GOOGLE_ADS_ID/GA4_ID, service/vehicle option lists (FR-017)
├── api/
│   ├── __init__.py
│   ├── quotes.py           # POST /api/quotes — User Story 1 (FR-001–FR-007, FR-001a)
│   └── paypal_webhook.py   # POST /api/paypal-webhook — User Story 2 (FR-008–FR-016)
├── services/
│   ├── paypal.py           # OAuth token cache, paypal_phone shape, create_draft_invoice (research.md #7)
│   ├── email.py             # send_email (Jinja2 render → HTML+text → SMTP); lead + operator-alert templates
│   ├── quotes.py            # quote persistence + lookup-by-id (FR-007, FR-011)
│   └── conversions.py       # click-id staleness gate, identifier resolution, idempotent write (FR-012–FR-016)
├── lib/
│   ├── hash_identifier.py    # hashEmail/hashPhone port (FR-014)
│   ├── extract_invoice_phone.py  # US phone extraction from invoice text (FR-013)
│   ├── webhook_auth.py        # PayPal transmission signature verification (FR-008/FR-009)
│   ├── access_control.py      # frontend shared-secret check (FR-001a)
│   └── time_format.py         # America/New_York formatting helpers (FR-018)
├── alerts.py                 # operator-alert triggers for FR-020/FR-021 (research.md #4)
├── validation/
│   └── contact.py             # pydantic request schema + refinements (service/vehicle enum, phone, pickup-time)
├── db.py                      # asyncpg pool + parameterized queries against quote_requests/conversions
└── templates/
    ├── lead_notification.html.jinja / .txt.jinja
    └── operator_alert.html.jinja / .txt.jinja

tests/
├── contract/
│   ├── test_quotes_contract.py       # exercises contracts/quote-submission.md
│   └── test_webhook_contract.py      # exercises contracts/paypal-webhook.md
├── integration/
│   ├── test_quote_submission_flow.py # end-to-end: submit → email + invoice + persisted row
│   └── test_webhook_conversion_flow.py # end-to-end: paid event → single conversion row
└── unit/
    ├── test_hash_identifier.py
    ├── test_extract_invoice_phone.py
    ├── test_click_id_staleness.py
    ├── test_webhook_auth.py
    └── test_contact_validation.py
```

**Structure Decision**: Single backend-service project under `src/cobalt_backend/` (matches the existing `pyproject.toml` entry point `cobalt-backend = "cobalt_backend:main"` and the empty `src/main.py` scaffold already in the repo — `main.py` will be superseded by the package layout above). No `frontend/` directory exists in or is added to this repository; the frontend remains a separate, already-existing application that calls this service over HTTP using the credential described in `contracts/quote-submission.md`.

## Complexity Tracking

*No entries — Constitution Check has no gates to violate (see above).*
