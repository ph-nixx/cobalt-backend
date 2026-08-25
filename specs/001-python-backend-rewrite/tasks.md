---

description: "Task list for feature implementation"
---

# Tasks: Lead Capture & Conversion Backend Rewrite

**Input**: Design documents from `/specs/001-python-backend-rewrite/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included — this repo already has colocated `<module>_test.py` tests for the webhook auth flow (`webhooks/paypal_test.py`), so the same convention is followed here.

**Organization**: Tasks are grouped by user story. Completed items are checked based on the current state of `src/` as of 2026-08-24 (see `git log`: `cf07e79` src/ restructure, `a0d587f` PayPal webhook auth, `99494d0` test logging).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 = Quote submission (P1), US2 = Conversion tracking (P2)
- Paths follow the flat `src/` layout in `plan.md` (not the originally-proposed `src/cobalt_backend/api|services|lib` layout)

---

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 Initialize `uv` project with Starlette + Uvicorn, no FastAPI (`pyproject.toml`, `main.py`)
- [ ] T002 Add remaining runtime dependencies to `pyproject.toml`: `asyncpg`, `phonenumbers`, `Jinja2`, `aiosmtplib` (research.md #2, #8, #9)
- [x] T003 [P] `main.py` assembles `routes` from `bookings/` and `webhooks/` into one Starlette app

**Checkpoint**: Server boots and serves both route groups (already true today).

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: Blocks both user stories below.

- [ ] T004 Extend `src/cfg.py` `Settings` with `DB_URL`, SMTP config, `FRONTEND_SHARED_SECRET`, `PHONE_DISPLAY`/`PHONE_TEL`, `COBALT_EMAIL`, `GOOGLE_ADS_ID`/`GA4_ID`, and the recognized service/vehicle option lists (FR-017)
- [ ] T005 Create `src/db.py`: `asyncpg` connection pool against the existing `quote_requests`/`conversions` tables; introspect live schema first per `data-model.md`'s caveat
- [ ] T006 [P] Create `src/hashing.py`: `hash_email`/`hash_phone` (FR-014), including email canonicalization (dots/`+`-suffix normalization per spec Edge Cases)
- [ ] T007 [P] Create `src/time_format.py`: business-local-timezone formatting helper (FR-018)
- [ ] T008 [P] Create `src/email.py`: `send_email(...)` — Jinja2 render to HTML+text, dispatch over SMTP/`aiosmtplib`
- [ ] T009 [P] Create `src/templates/lead_notification.{html,txt}.jinja`
- [ ] T010 Configure stdlib `logging` for structured (JSON) output per research.md #6, scoped to the failure points in FR-022 (not blanket logging)

**Checkpoint**: Config, DB pool, hashing, and email are available for both stories.

---

## Phase 3: User Story 1 — Quote Submission (Priority: P1) 🎯 MVP

**Goal**: Accept a valid quote, always send the lead email, attempt an invoice, defer persistence (spec User Story 1).

**Independent Test**: POST a valid submission → lead email arrives, response includes `quote_id`, row appears in `quote_requests` shortly after — independent of invoice success.

### Tests for User Story 1

- [x] T011 [P] [US1] Body-shape validation test coverage exists implicitly via `Submission` model (expand — see T017)
- [ ] T012 [P] [US1] Contract test for `POST /api/bookings` in `src/bookings/submission_test.py` — covers `contracts/quote-submission.md`'s 201/400/401 cases

### Implementation for User Story 1

- [x] T013 [US1] `Submission` pydantic model with `email`, `phone`, `vehicle`, `gclid`/`gbraid`/`wbraid`, `first_click` in `src/bookings/submission.py`
- [ ] T014 [US1] Add `service` (enum against `cfg.Settings` service list, FR-017) and `pickup_time` (ISO 8601, ≥1hr-from-now refinement, FR-001) fields to `Submission`; decide fate of the current undocumented `notes` field
- [ ] T015 [US1] Implement FR-001a shared-secret check (`Authorization: Bearer <FRONTEND_SHARED_SECRET>`) in `src/bookings/submission.py`, running **before** body validation; missing/wrong → `401`, no side effects (contract's "no email/no row" requirement)
- [ ] T016 [US1] Reject invalid bodies with `400` + per-field messages (contract shape: `{"errors": [{"field", "message"}]}`) instead of the current always-`200 {"error": "OK"}` stub
- [ ] T017 [US1] Add `create_draft_invoice(...)` to `src/paypal.py` — OAuth token cache (process-lifetime) + `POST /v2/invoicing/invoices`, bounded to 5s (FR-004/SC-002); on timeout/failure, proceed without an invoice reference
- [ ] T018 [US1] Wire lead-notification send in `src/bookings/submission.py`: render `templates/lead_notification.*`, include formatted pickup time (`time_format.py`) and invoice link or plain quote-id reference (FR-005); on send failure, fail the request (`500`, FR-006) — this is the one non-swallowed failure mode
- [ ] T019 [US1] Return `201 {"quote_id", "invoice_url"?}` on success, matching `contracts/quote-submission.md`
- [ ] T020 [US1] Persist the quote row (`db.py`) as a Starlette `BackgroundTask` after the response is built (FR-007); fix `Submission.as_row()` to include `phone` and `gbraid` (currently dropped) and match the real `quote_requests` columns from T005's schema introspection
- [ ] T021 [US1] Remove the stale commented-out email imports at the top of `src/bookings/submission.py`

**Checkpoint**: User Story 1 fully functional and independently testable.

---

## Phase 4: User Story 2 — Conversion Tracking (Priority: P2)

**Goal**: Verify PayPal "invoice paid" webhooks, rejoin the lead, write one idempotent, hashed conversion entry (spec User Story 2).

**Independent Test**: Send a signed `INVOICE.PAID` event for a known quote → exactly one `conversions` row with hashed identifiers; resend → no duplicate.

### Tests for User Story 2

- [x] T022 [P] [US2] Signature-verification tests in `src/webhooks/paypal_test.py` (valid payload, bad headers, malformed JSON)
- [ ] T023 [P] [US2] Add tests for: non-`https`/non-`*.paypal.com` cert URL rejection, non-`PAID` event ack-without-processing, idempotent duplicate write, click-id staleness gate, no-usable-identifiers path

### Implementation for User Story 2

- [x] T024 [US2] `PaypalAuthHeaders` + `_request_auth_protocol`: RSA/SHA-256 signature verification against fetched cert (`src/webhooks/paypal.py`)
- [ ] T025 [US2] Restrict `paypal-cert-url` to `https://` on `*.paypal.com` before fetching (FR-008 gap — currently any `HttpUrl` is accepted and fetched)
- [ ] T026 [US2] Replace `PaypalEvent`'s hard `Literal["INVOICING.INVOICE.PAID"]` (which raises a validation error on other event types) with acknowledge-and-skip handling for all other event types, plus sandbox `INVOICING.INVOICE.SENT` support behind config (FR-010)
- [ ] T027 [US2] Look up `quote_requests` by the invoice's `detail.reference` via `db.py` (FR-011)
- [ ] T028 [US2] Click-id staleness gate: exclude `gclid`/`gbraid`/`wbraid` when `first_click` is missing or >30 days before the event (FR-012)
- [ ] T029 [US2] Resolve a phone number from the matched quote, else extract one from invoice text (excluding `PHONE_TEL`) via a new `extract_invoice_phone` helper (FR-013)
- [ ] T030 [US2] Hash resolved email/phone via `src/hashing.py` (FR-014); never store raw identifiers
- [ ] T031 [US2] Idempotent write: `INSERT ... ON CONFLICT (txn) DO NOTHING` into `conversions` via `db.py` (FR-015)
- [ ] T032 [US2] Change `record_payed_invoice` to always return `200 {"received": true}` regardless of outcome (FR-016) — currently returns the parsed event body, which also leaks internal state to the caller
- [ ] T033 [US2] Add `src/alerts.py`: operator-alert email (reuse `email.py` + `templates/operator_alert.*`) for verification failure (FR-020) and no-usable-identifiers (FR-021); include basic de-dup/rate-limiting per research.md #4's follow-up note
- [ ] T034 [P] [US2] Create `src/templates/operator_alert.{html,txt}.jinja`

**Checkpoint**: Both user stories independently functional.

---

## Phase 5: Polish & Cross-Cutting Concerns

- [ ] T035 Run `quickstart.md` end-to-end against both endpoints
- [ ] T036 Side-by-side parity check against the current Next.js system per SC-007 before cutover
- [ ] T037 [P] Add `.env.example` covering all `cfg.Settings` fields (referenced by `quickstart.md`)

---

## Dependencies & Execution Order

- **Setup (Phase 1)** → mostly done; T002 has no dependencies.
- **Foundational (Phase 2)** blocks both user stories; T004–T010 can mostly run in parallel except T005 (db.py) should land before anything in Phase 3/4 that writes to it.
- **User Story 1 (Phase 3)** and **User Story 2 (Phase 4)** are independent of each other and can proceed in parallel once Phase 2 is done — US1 is recommended first since it's further from complete and is the P1/MVP story.
- **Polish (Phase 5)** depends on both stories being functionally complete.

## Implementation Strategy

**MVP = User Story 1.** Webhook signature verification (the hardest single piece of User Story 2) is already built and tested, so User Story 2's remaining work (T025–T034) is comparatively mechanical once `db.py`/`hashing.py` exist from Phase 2. Recommended order: Phase 2 → Phase 3 (US1) → validate independently → Phase 4 (US2) → Phase 5.
