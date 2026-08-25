---

description: "Task list for feature implementation"
---

# Feature Specification: Bookings Submission Endpoint

**Feature Branch**: `003-bookings-post-endpoint`

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "I have decided that the most important feature to focus on is the 'api/bookings' POST endpoint because this endpoint has the most moving parts: 1. we have to consider validation performance a little bit because the client can only proceed after their submission has been validated by the server 2. the endpoint has to write to the database 3. the endpoint has to send an email to the business 4. we have to provide insightful json content about what failed to the client (the Next.js frontend) so it can correctly inform the user what to fix"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Business receives the lead immediately (Priority: P1)

A customer fills out a service quote request (vehicle, service type, pickup time, and contact details) and submits it. Once the submission is accepted, the business is notified right away with the customer's details so operations staff can follow up quickly, and the customer sees confirmation that their request went through.

**Why this priority**: This is the entire point of the booking flow. If a valid request doesn't reliably reach the business, the company loses a real, ready-to-pay customer — regardless of how well anything else in the system works.

**Independent Test**: Submit a fully valid quote request and confirm the business receives a notification containing the correct request details, and the customer receives a success confirmation.

**Acceptance Scenarios**:

1. **Given** a customer has filled in all required trip details correctly, **When** they submit the request, **Then** the business receives a notification containing the customer's contact info and requested trip details, and the customer sees a success confirmation.
2. **Given** a customer submits a valid request, **When** the system finishes processing it, **Then** the request is retrievable in the business's records for later reference and reporting.

---

### User Story 2 - Customers get clear, actionable guidance when something needs fixing (Priority: P1)

When a customer's submission is missing information or contains an invalid value (for example, a pickup time that's too soon, or a badly formatted phone number), the system tells the customer exactly what's wrong, field by field, instead of a generic failure message.

**Why this priority**: Poor error feedback causes customers to abandon the request outright or contact support in frustration — either way, the lead is at risk of being lost. This is the second-most valuable safeguard after successfully capturing valid leads at all, since the frontend depends entirely on this feedback to guide the customer.

**Independent Test**: Submit a request with two intentionally invalid fields and confirm the response identifies both specific fields with an explanation of what's wrong with each, and confirm no notification is sent and no lead is recorded.

**Acceptance Scenarios**:

1. **Given** a customer omits a required field, **When** they submit, **Then** the response names that specific field and explains what's required.
2. **Given** a customer's submission has more than one problem at once, **When** they submit, **Then** the response lists every problem found, not just the first one encountered.
3. **Given** a customer's submission is invalid, **When** they submit, **Then** no business notification is sent and no lead is recorded from that attempt.
4. **Given** a customer corrects every problem identified in the response, **When** they resubmit, **Then** the request succeeds (assuming no new problems were introduced).

---

### User Story 3 - Leads are safely recorded even when optional steps don't complete (Priority: P2)

The business needs a durable, complete record of every accepted quote request for reporting and lead tracking, even in situations where an optional downstream step (such as generating a payment invoice) doesn't finish in time.

**Why this priority**: The business relies on this record to measure marketing/ad performance and reconcile leads later. It's lower priority than Story 1 and 2 because it protects data completeness for reporting rather than the core capture of the lead itself, which is already guaranteed by Story 1.

**Independent Test**: Submit a valid request while a downstream optional step is deliberately unavailable, and confirm the request is still accepted, still recorded, and the customer still receives a success confirmation.

**Acceptance Scenarios**:

1. **Given** an optional downstream step (e.g., invoice generation) is slow or unavailable, **When** a customer submits an otherwise-valid request, **Then** the customer still receives a success confirmation and the request is still recorded as a lead.

---

### Edge Cases

- What happens when a submission passes validation but the business notification cannot be sent (e.g., an outage)? No lead is recorded and the customer is told the request did not go through; the failure is logged and an internal devops alert is sent so the outage doesn't go unnoticed (FR-008).
- What happens when a customer submits the exact same request twice in a row (e.g., a double-click)? Each submission is treated independently; no duplicate-detection is performed for this feature.
- What happens when the requested pickup time is in the past or too soon to be honored? The submission is rejected as invalid with an explanation.
- What happens when the requested service or vehicle type isn't one the business currently offers? The submission is rejected as invalid with an explanation.
- What happens when a request is missing all identifying contact information? The submission is rejected as invalid, naming every missing required field.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST validate every submission's required fields (customer contact details, vehicle and service selection, requested pickup time) before accepting it.
- **FR-002**: System MUST reject a submission whose requested pickup time is not far enough in the future to realistically be honored.
- **FR-003**: System MUST reject a submission specifying a service or vehicle type that is not one of the business's currently offered options.
- **FR-004**: When validation fails, system MUST return a response that identifies every invalid or missing field individually, each paired with a human-readable explanation of what needs to change, so the frontend can point the customer directly at each problem.
- **FR-005**: System MUST NOT notify the business or record a lead for any submission that fails validation.
- **FR-006**: System MUST notify the business promptly after a submission passes validation, and the notification MUST include the customer's contact details and the requested trip details.
- **FR-007**: System MUST confirm success to the customer only once the business has actually been notified of the new lead — a customer-facing success confirmation always means the business also knows about the request.
- **FR-008**: When a submission passes validation but the business notification cannot be sent, system MUST NOT record a lead from that submission — the customer is told the request did not go through and must resubmit. This failure MUST be logged and MUST trigger an internal alert email to the devops/operations team, so a notification outage is never silent.
- **FR-009**: System MUST durably record every accepted submission as a lead in the business's records, regardless of whether an optional downstream step (e.g., invoice generation) completes.
- **FR-010**: System MUST only accept submissions originating from the business's own customer-facing site, not from arbitrary public callers.
- **FR-011**: System MUST preserve any advertising-attribution identifiers included with a submission, for later marketing/reporting use, without exposing a customer's raw contact information in that reporting.
- **FR-012**: System MUST respond to submission attempts promptly enough that the customer does not perceive the site as unresponsive or stuck while waiting (see SC-001).

### Key Entities

- **Quote Request (Lead)**: A single customer's request for service — includes contact details, requested service/vehicle/pickup time, any advertising-attribution identifiers, and when it was submitted. This is the record the business relies on for follow-up and reporting.
- **Business Notification**: The outbound message that alerts operations staff that a new lead has come in and needs a response.
- **Devops Failure Alert**: A distinct outbound message to the devops/operations team, separate from the Business Notification, raised specifically when a Business Notification fails to send — signals a system-level problem needing attention, not a lead needing follow-up.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Customers submitting a fully valid request receive a success confirmation within 2 seconds in the large majority (95%+) of submissions, so the site never feels stuck while they wait.
- **SC-002**: 100% of submissions that pass validation result in either a business notification being sent or the customer being clearly told the request did not go through — no valid submission silently disappears.
- **SC-003**: A customer correcting a rejected submission sees every problem with their submission identified on their very next attempt, without needing multiple rounds of trial-and-error to discover additional errors one at a time.
- **SC-004**: Zero accepted leads are lost due to failure of an unrelated, optional step (e.g., invoice generation).
- **SC-005**: The business's lead records are complete — every accepted submission is present and retrievable for reporting.

## Assumptions

- The set of service and vehicle options a customer may choose from is defined and maintained elsewhere (existing business configuration) rather than being specified by this feature.
- Duplicate or repeated submissions from the same customer (e.g., an accidental double-click) are not deduplicated as part of this feature.
- The customer-facing site is a trusted, known caller of this endpoint; this feature does not need to support arbitrary third-party or public API consumers.
- "Insightful" error content (FR-004) means field-level, human-readable explanations returned as structured data the frontend can map directly onto its form fields — not free-form prose the frontend would need to parse.
