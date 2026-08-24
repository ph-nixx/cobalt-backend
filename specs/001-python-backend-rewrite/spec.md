# Feature Specification: Lead Capture & Conversion Backend Rewrite

**Feature Branch**: `001-python-backend-rewrite`

**Created**: 2026-08-22

**Status**: Draft

**Input**: User description: "I'm going to be doing a rewrite of my Next.js backend in python; here is a description of the majority of the server side functionality" covering the PayPal payment webhook / Google Ads conversion write path, the contact/quote form submission path (draft invoice, lead email, quote persistence), supporting libraries (PayPal invoicing, email dispatch and templating, contact validation, identifier hashing, phone extraction), and static shared configuration used by both paths.

## Clarifications

### Session 2026-08-22

- Q: Should the rewritten backend keep writing to the exact same production database and tables the current Next.js system uses, or is a fresh/separate database acceptable? → A: Same production database/tables as today. The old (Next.js) system keeps running unchanged until the rewrite is fully built, tested, and deployed, so both systems can run against the same live data during the transition and cutover is seamless.
- Q: During the transition, could the old and new backends ever both process the same incoming request (same form submission or same webhook delivery) at the same time, or will traffic be routed to only one system at a time? → A: Only one backend receives live traffic at a time — the switch from old to new is a clean cutover, not a gradual/canary rollout, so the old and new systems never race on the same incoming request.
- Q: Does the rewrite need to add monitoring/alerting so a human finds out when something fails silently (e.g., the webhook rejecting a notification as unverified, or a paid invoice producing a conversion entry with no usable contact identifiers), or is silent handling acceptable as today? → A: In scope — the rewrite must notify a human operator when these silent-failure conditions occur. Observability should be intentional and scoped to key failure points rather than blanket/exhaustive logging of every request.
- Q: Does the public quote-submission form have any existing spam/abuse protection (CAPTCHA, rate limiting, bot filtering) that needs to be preserved in the rewrite? → A: No abuse protection exists today; none is required by this rewrite. It remains out of scope, consistent with the like-for-like nature of this rewrite.
- Q: Should the rewritten backend reject quote-submission requests that don't come from the business's own authorized frontend, as a hard security requirement — or is input validation alone sufficient, matching the PayPal webhook's posture? → A: Hard requirement. The backend MUST reject quote-submission requests not originating from the authorized frontend client, in addition to normal input validation. This restores parity with the current system, where the submission handler is a same-origin Server Action rather than a directly-callable public endpoint; the rewrite must not silently regress this protection by exposing an open HTTP endpoint. The specific access-restriction mechanism (e.g., private networking between frontend and backend, a shared secret, mutual TLS) is a deployment/architecture decision for the planning phase, not the spec.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Prospective Customer Submits a Quote Request (Priority: P1)

A prospective customer fills out the contact/quote form on the website with their service needs, vehicle type, pickup time, and contact details, and submits it. The business needs to receive that lead reliably — by email — even if secondary steps (creating a payable invoice, saving the lead to the database, or recovering ad-click identifiers) fail or are slow.

**Why this priority**: This is the core revenue-generating flow. Every other capability in this system exists to support or enrich this one. If this breaks, the business stops receiving leads.

**Independent Test**: Can be fully tested by submitting a valid quote form and confirming (a) the submission is accepted, (b) a lead notification email arrives at the business inbox with the customer's details, and (c) the request is retrievable afterward as a stored quote — independent of whether invoice creation or ad-click identifier capture succeeded.

**Acceptance Scenarios**:

1. **Given** a customer fills out the form with a valid service, vehicle type, pickup time at least one hour in the future, and valid contact details, **When** they submit, **Then** the system accepts the submission, generates a payable invoice reference for the request, and sends a lead notification email to the business containing the customer's details and a link to the invoice.
2. **Given** a customer submitted the form earlier after arriving via a tracked ad click, **When** they submit, **Then** the system captures the ad-click identifiers present at submission time alongside the lead so they can later be matched to a payment.
3. **Given** invoice creation is slow or unavailable, **When** the customer submits a valid form, **Then** the system still sends the lead notification email (referencing the request without an invoice link) within a bounded wait time, rather than making the customer wait indefinitely.
4. **Given** a customer enters a pickup time less than one hour from now, **When** they submit, **Then** the system rejects the submission with a clear validation message and does not create a lead.
5. **Given** a customer enters a phone number that cannot be recognized as valid, **When** they submit, **Then** the system rejects the submission with a clear validation message.

---

### User Story 2 - Business Tracks Ad Conversions When an Invoice Is Paid (Priority: P2)

When a customer pays the invoice generated from their quote request, the payment provider notifies the system. The system must confirm the notification is genuinely from the payment provider, reconnect the payment to the original lead (and any ad-click identifiers captured at submission time), and record a single, privacy-safe conversion entry that the business's ad platform can later ingest to measure ad performance.

**Why this priority**: This closes the loop between ad spend and revenue. It's essential to the business's ability to measure and optimize marketing, but a delay or transient failure here does not block any customer-facing interaction — it can be secondary to lead capture.

**Independent Test**: Can be fully tested by simulating a genuine "invoice paid" notification for a known quote request and confirming exactly one conversion entry is recorded with the correct identifiers, and that re-sending the same notification does not create a duplicate.

**Acceptance Scenarios**:

1. **Given** a genuine "invoice paid" notification for an invoice tied to a known quote request, **When** the system receives it, **Then** it records exactly one conversion entry containing privacy-safe (hashed) contact identifiers and, if still fresh, the ad-click identifiers captured at submission time.
2. **Given** the same "invoice paid" notification is delivered more than once (a common behavior of payment-provider webhooks), **When** the system processes the duplicate, **Then** no additional conversion entry is created.
3. **Given** a notification whose authenticity cannot be verified, **When** the system receives it, **Then** it is rejected without being processed and without creating a conversion entry.
4. **Given** a notification for an event type the business does not act on, **When** the system receives it, **Then** it acknowledges receipt without creating a conversion entry or reporting an error.
5. **Given** the ad-click identifiers on the matched lead were captured more than 30 days before the invoice was paid, **When** the system records the conversion, **Then** it omits the stale ad-click identifiers but still records the conversion using privacy-safe contact identifiers.
6. **Given** no quote request can be matched to the paid invoice, **When** the system records the conversion, **Then** it falls back to any contact details present in the payment notification itself (e.g., a phone number recoverable from invoice text) rather than dropping the conversion entirely.

---

### Edge Cases

- What happens when a payment notification claims to come from the payment provider but its verification certificate is hosted somewhere other than the provider's own domain? System must reject it.
- What happens when the database is temporarily unavailable while trying to record a conversion? The payment provider must still receive a successful acknowledgment so it does not retry-storm the endpoint; the conversion is simply not recorded for that event.
- What happens when the database is temporarily unavailable while trying to persist a newly submitted quote (after the lead email already sent)? The customer-facing submission must not fail because of this — the lead has already reached the business by email.
- What happens when no contact identifier (neither a matched quote's email/phone nor an extractable phone from the invoice) is available at all for a paid invoice? The system records what it can (which may be limited to ad-click identifiers, if still fresh) rather than failing the acknowledgment.
- What happens when a customer's email domain uses address formatting quirks (e.g., dots or "+" suffixes in the local part) that could otherwise cause the same person to be hashed as two different identities? The system must normalize these consistently before hashing.
- How does the system handle a submission where the customer arrived with no ad-click identifiers at all (organic/direct traffic)? The lead and, later, its conversion must still be captured successfully, simply without ad-click identifiers.
- What happens when the lead notification email fails to send? The submission as a whole must fail and the customer must be informed, since this is the business's only guaranteed path to receiving the lead.
- What happens when a quote-submission request arrives that does not originate from the authorized frontend client (e.g., a direct call from an arbitrary internet client)? System must reject it before validation or processing, and must not create a lead, invoice, or notification.

## Requirements *(mandatory)*

### Functional Requirements

**Quote submission**

- **FR-001**: System MUST validate every quote/contact submission server-side against the authoritative rules (recognized service and vehicle options, a contact phone number normalizable to a standard international format, and a pickup time at least one hour in the future), independent of any client-side validation.
- **FR-001a**: System MUST reject quote-submission requests that do not originate from the business's own authorized frontend client, before applying the validation rules in FR-001. This restores the access-control posture of the current system, where the submission handler is not directly callable by arbitrary internet clients.
- **FR-002**: System MUST reject invalid submissions with a clear indication of what failed, and MUST NOT create a lead, invoice, or notification for a rejected submission.
- **FR-003**: System MUST capture ad-click identifiers and the timestamp of the customer's first tracked visit, when present at submission time, and associate them with the quote request.
- **FR-004**: System MUST attempt to create a payable, zero-amount draft invoice referencing the quote request, bounded by a maximum wait time, and MUST proceed with the submission (without an invoice reference) if invoice creation does not complete in time or fails.
- **FR-005**: System MUST send a lead notification email to the business's designated inbox for every accepted submission, including the customer's contact details, formatted pickup time (in the business's local time zone), and either a link to the created invoice or a plain reference to the request if no invoice was created.
- **FR-006**: System MUST fail the submission and inform the customer if the lead notification email cannot be sent.
- **FR-007**: System MUST persist every accepted submission (contact details, ad-click identifiers, and first-visit timestamp) as a retrievable quote request, and this persistence step MUST NOT block or fail the customer-facing response, nor cause the submission to appear to fail if persistence itself fails after the notification email was already sent.

**Conversion tracking**

- **FR-008**: System MUST verify that every incoming "invoice paid" notification is authentically from the payment provider before acting on it, including confirming the notification's supporting certificate is retrieved only from the payment provider's own domain over a secure connection.
- **FR-009**: System MUST reject notifications that fail authenticity verification without creating a conversion entry, and MUST NOT expose the reason for rejection to the sender.
- **FR-010**: System MUST act only on "invoice paid" notifications (and, in a designated test mode, an equivalent "invoice sent" notification); all other event types MUST be acknowledged successfully without further processing.
- **FR-011**: System MUST attempt to match an incoming paid-invoice notification back to the original quote request using the reference recorded at invoice-creation time, to recover the customer's contact details and any ad-click identifiers.
- **FR-012**: System MUST exclude ad-click identifiers from a conversion entry when the associated first-visit timestamp is missing or is more than 30 days before the payment notification, while still recording the conversion using privacy-safe contact identifiers.
- **FR-013**: System MUST resolve a contact phone number for the conversion entry from the matched quote request when available, and otherwise attempt to recover one from the text of the invoice itself, excluding the business's own phone number from consideration.
- **FR-014**: System MUST convert every contact identifier (email, phone) into a privacy-safe, one-way hashed form before storing it in a conversion entry, using normalization rules (e.g., email address canonicalization) consistent with the ad platform's identity-matching requirements. Raw, unhashed contact identifiers MUST NOT be stored in a conversion entry.
- **FR-015**: System MUST record at most one conversion entry per unique payment transaction, so that repeated delivery of the same notification (a normal behavior of payment-provider webhooks) never produces duplicate entries.
- **FR-016**: System MUST acknowledge every well-formed, authenticated notification successfully (regardless of downstream storage outcome) so the payment provider does not treat the endpoint as failing and retry excessively.

**Shared / supporting behavior**

- **FR-017**: System MUST expose the same set of recognized service and vehicle options to both submission validation and any presentation of choices to the customer, so the two never diverge.
- **FR-018**: System MUST format all customer-facing and business-facing pickup/received times in the business's local time zone regardless of the submitter's or server's time zone.
- **FR-019**: System MUST treat the business's own published phone number as never a valid "customer" identifier when scanning free-text invoice content for a phone number.

**Observability**

- **FR-020**: System MUST notify a human operator when a payment notification fails authenticity verification, so that a systemic problem (e.g., a broken verification chain silently rejecting legitimate payments) does not go unnoticed.
- **FR-021**: System MUST notify a human operator when a conversion entry is recorded with no usable contact identifiers at all (neither hashed email/phone nor fresh ad-click identifiers), so silent identifier-resolution failures are visible.
- **FR-022**: System MUST apply deliberate, scoped observability (e.g., structured logging or alerting) to the key failure points identified in this spec (webhook verification failure, invoice-creation timeout/failure, lead notification email failure, quote/conversion persistence failure) rather than exhaustive logging of all requests and data.

### Key Entities

- **Quote Request**: A prospective customer's submitted lead — service and vehicle selection, requested pickup time, contact email and phone, captured ad-click identifiers, first-visit timestamp, and a reference usable to later match a payment to this lead.
- **Draft Invoice**: A zero-amount, payable invoice created against a Quote Request, used both to give the customer a way to pay and to carry the reference the payment notification uses to rejoin the lead.
- **Conversion Entry**: A single, idempotent record created when an invoice is paid — privacy-safe (hashed) contact identifiers, ad-click identifiers (if still fresh), and a unique transaction reference preventing duplicates.
- **Lead Notification**: The business-facing email generated per accepted submission, containing the customer's details, formatted pickup time, and either an invoice link or a plain reference.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of validly submitted quote requests result in a lead notification reaching the business inbox, regardless of whether invoice creation or lead persistence succeeds.
- **SC-002**: Invoice creation never delays a customer's submission response by more than 5 seconds, even when the payment provider is slow or unreachable.
- **SC-003**: 100% of forged or unverifiable payment notifications are rejected without producing a conversion entry.
- **SC-004**: 100% of repeated deliveries of the same genuine payment notification produce exactly one conversion entry, never more.
- **SC-005**: 0% of stored conversion entries contain raw (non-hashed) customer contact identifiers.
- **SC-006**: 100% of submissions with a requested pickup time less than one hour away are rejected before a lead is created.
- **SC-007**: For equivalent inputs, the rewritten system's outward behavior (validation outcomes, email content, invoice creation, and conversion entries produced) is indistinguishable from the current system's, verified by side-by-side comparison before cutover.
- **SC-008**: 100% of webhook authenticity-verification failures and 100% of conversion entries recorded with no usable contact identifiers generate a notification a human operator can act on, without requiring the operator to manually inspect raw logs to discover the condition.
- **SC-009**: 100% of quote-submission requests not originating from the authorized frontend client are rejected before any lead, invoice, or notification is created.

## Assumptions

- This rewrite covers server-side/backend logic only (the webhook receiver, the quote submission handler, and their supporting logic). Customer-facing page rendering is out of scope and is assumed to continue operating as-is, calling into the rewritten backend rather than being rewritten itself.
- The rewritten backend continues to read from and write to the same underlying data store and tables (quote requests, conversion entries) rather than migrating to a new data store, since no change to data storage was requested. The current (Next.js) system remains live and unchanged against this same data store until the rewrite is fully built, tested, and deployed, allowing a seamless cutover with no data migration step.
- The payment provider (PayPal-style invoicing and webhook notifications) and the ad platform's conversion-matching requirements (privacy-safe hashed identifiers) remain the same third-party integrations as today; only the implementing language/runtime changes.
- Email delivery continues via the same outbound mechanism/provider (e.g., an SMTP-based service) rather than switching providers, since no change was requested.
- A test/sandbox mode for the payment-provider integration (accepting an additional event type and an additional, separately configured verification identifier) continues to be supported behind explicit configuration, mirroring current behavior.
- "30 days" is treated as the fixed staleness threshold for ad-click identifiers, and "1 hour" as the fixed minimum lead time for pickup requests, matching current behavior; neither is intended to become user-configurable as part of this rewrite.
- The business's published phone number and notification inbox address remain fixed configuration values rather than becoming user-editable.
- Cutover from the old (Next.js) system to the rewritten one is a clean, one-time switch of live traffic (not a gradual/canary rollout), so the two systems never simultaneously process the same incoming form submission or webhook delivery; the rewrite therefore does not need to handle cross-system duplicate detection.
- No spam/abuse protection (CAPTCHA, rate limiting, bot filtering) exists on the quote-submission form today, and adding one is out of scope for this rewrite.
- The rewritten backend is expected to be deployed such that it and its frontend can share a private network boundary not reachable from the public internet by default; the exact access-restriction mechanism (network isolation, shared secret, mutual TLS, or a combination) is a deployment/architecture decision to be made during planning, not fixed by this spec.
