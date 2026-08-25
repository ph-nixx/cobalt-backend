# Feature Specification: Production Logging & Alerting

**Feature Branch**: `002-production-logging`

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "the production server will need a minimal json stdio and email logging system build with the native python logger package"

## Clarifications

### Session 2026-08-24

- Q: What should trigger an email alert (vs. just a stdout log line)? → A: Any error-or-higher-severity event, anywhere in the server, triggers an operator email — not just the two conditions originally named in `001-python-backend-rewrite` (FR-020/FR-021). Those two remain examples of what will produce alerts, not the exhaustive list.
- Q: Should repeated alerts for the same failure be coalesced so a burst doesn't flood the operator's inbox? → A: Yes. At most one alert email per distinct condition within a cooldown window; further occurrences of the same condition during the window are still logged but do not send another email.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operator Diagnoses an Issue from Console Output (Priority: P1)

While investigating a production issue (a customer complaint, an unexpected pattern, or a routine check), an operator reviews the server's console output and needs every significant event — requests, failures, and background outcomes — recorded there in a consistent, structured form they (or the hosting platform's log tooling) can search and filter.

**Why this priority**: Every other observability capability in this system depends on events being recorded in the first place. Without this, there is nothing to alert on and nothing to investigate after the fact.

**Independent Test**: Trigger a range of server events (a normal request, a validation failure, a downstream failure) and confirm each produces one structured, parseable record on standard output containing enough information (when, what, severity, where) to understand what happened without cross-referencing other systems.

**Acceptance Scenarios**:

1. **Given** the server is running, **When** any significant event occurs (a request is handled, a validation fails, a downstream call fails), **Then** a single structured record is written to standard output containing at minimum a timestamp, a severity level, the originating component, and a human-readable message.
2. **Given** an event involves customer contact information, **When** the record is written, **Then** it does not contain the customer's raw (unhashed/unmasked) email or phone number.
3. **Given** the server's standard output is being collected by the hosting platform's log pipeline, **When** records are written, **Then** they are usable by that pipeline without additional parsing rules or configuration.

---

### User Story 2 - Operator Is Notified When Something Needs Attention (Priority: P2)

When the server encounters a problem serious enough to warrant a human looking at it — not just routine request-level noise — an operator receives an email describing what happened, without having to actively watch console output or dig through logs to notice the problem exists.

**Why this priority**: Console logs alone are only useful if someone is watching them. This closes the "silent failure" gap: today, several classes of failure (a forged webhook notification, a payment with no recoverable contact identifiers, and others as the system grows) can occur without anyone finding out. This depends on User Story 1 existing (there must be something to alert from) but is what actually gets a human's attention.

**Independent Test**: Trigger a server-side error condition and confirm an email arrives at the operator's configured inbox containing enough context (what happened, when, and where) to begin investigating without first opening raw logs.

**Acceptance Scenarios**:

1. **Given** the server logs an event at error severity or higher, **When** that event is recorded, **Then** an email is sent to the operator's designated inbox describing the condition, unless an email for the same condition was already sent within the cooldown window (see Scenario 3).
2. **Given** an alert email is sent, **When** the operator reads it, **Then** it contains enough context (timestamp, what failed, where) to begin investigating without inspecting raw console output first.
3. **Given** the same underlying condition recurs multiple times in quick succession (e.g., a sustained burst of the same failure), **When** each occurrence is logged, **Then** only the first occurrence in a cooldown window sends an email; subsequent occurrences within that window are still logged to standard output but do not send additional emails.
4. **Given** the cooldown window for a condition has elapsed and the condition recurs, **When** it is logged again, **Then** a new alert email is sent.
5. **Given** two different failure conditions occur around the same time, **When** they are logged, **Then** each is evaluated against its own independent cooldown — one condition's cooldown does not suppress an alert for a different condition.

---

### Edge Cases

- What happens when the outbound email delivery mechanism itself is unreachable or fails while sending an alert? The failure to alert must itself be logged to standard output, and must not block, delay, or fail whatever request or background operation triggered the original event.
- What happens when writing to standard output itself is blocked or fails (e.g., a full or broken output stream)? The application must not crash or hang because of a logging failure; it should continue serving requests on a best-effort basis.
- What happens when an event's contextual details themselves contain sensitive values (e.g., a raw contact identifier passed as extra context, not just the top-level message)? Those values must be excluded or masked before the record is written or emailed, consistent with the "no raw identifiers" rule.
- How is a "distinct condition" identified for cooldown purposes, when the same general failure type occurs for different underlying reasons or different affected records? Each distinct condition is tracked separately (see FR-006) so that, for example, two different customers' unrelated failures are not silently conflated into a single suppressed alert.
- What happens at server startup/shutdown, or if the alerting mechanism's own configuration is missing/invalid? The server must still start and serve requests; a misconfigured alerting path degrades to stdout-only logging with that condition itself logged, rather than crashing the server.

## Requirements *(mandatory)*

### Functional Requirements

**Structured console logging**

- **FR-001**: System MUST write a structured, machine-parseable record to standard output for every significant application event, including at minimum a timestamp, a severity level, the originating component/module, and a human-readable message.
- **FR-002**: System MUST support at least the standard range of severity levels (e.g., debug/info through error/critical) so events can be filtered by importance.
- **FR-003**: System MUST NOT include raw (unhashed/unmasked) customer contact identifiers or other sensitive values in any console record, whether in the primary message or any attached contextual detail.
- **FR-004**: Console logging MUST be usable as a single, shared capability that any part of the server can invoke consistently, rather than each part of the server implementing its own ad hoc logging.

**Email alerting**

- **FR-005**: System MUST send an email to the operator's designated inbox whenever an event is logged at error severity or higher, describing what happened with enough context (timestamp, condition, originating component) for a human to begin investigating without first consulting raw console output.
- **FR-006**: System MUST treat each distinct failure condition independently for alerting purposes, so that suppressing repeat alerts for one condition (FR-007) never suppresses alerts for an unrelated condition.
- **FR-007**: System MUST suppress repeated alert emails for the same distinct condition within a cooldown window, sending at most one alert email per condition per window; occurrences during the window MUST still be recorded via FR-001, just without an additional email.
- **FR-008**: System MUST send a new alert email for a condition that recurs after its cooldown window has elapsed.
- **FR-009**: A failure to send an alert email MUST itself be recorded via FR-001 and MUST NOT block, delay, or fail the operation that triggered the original event.
- **FR-010**: System MUST NOT require standing up or paying for a new third-party logging or alerting service; alerting reuses the outbound email capability the server already has for other purposes.

**Reliability**

- **FR-011**: A failure within the logging or alerting capability itself (e.g., the output stream is unavailable) MUST NOT crash the server or interrupt the request/operation that produced the event being logged.

### Key Entities

- **Log Record**: A single structured entry describing one significant event — timestamp, severity level, originating component, human-readable message, and safe (non-sensitive) contextual details.
- **Alert Condition**: A distinct, identifiable class of error-or-higher event tracked independently for cooldown purposes (e.g., "webhook verification failed," "conversion recorded with no usable identifiers," or any other error-severity condition that arises).
- **Operator Alert Email**: The notification sent to the operator's inbox for a given Alert Condition's first occurrence within a cooldown window — timestamp, condition description, and enough context to begin investigating.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of significant server events produce a structured console record containing timestamp, severity, source, and message.
- **SC-002**: 0% of console records or alert emails contain a raw (unhashed/unmasked) customer contact identifier.
- **SC-003**: 100% of error-or-higher-severity events result in an operator alert email, except those correctly suppressed by an active cooldown for the same condition.
- **SC-004**: A sustained burst of 50 occurrences of the same failure condition within one cooldown window produces exactly 1 alert email, not 50.
- **SC-005**: An operator can determine what failed, when, and where from an alert email alone, without opening raw console output, for 100% of alerts sent.
- **SC-006**: The logging/alerting capability introduces no new paid third-party service and no new required infrastructure beyond what the server already uses for console output and email delivery.
- **SC-007**: A failure in email delivery or in the logging mechanism itself never causes a customer-facing request to fail or hang because of the logging/alerting code path.

## Assumptions

- This feature formalizes and generalizes the observability requirements already identified for the quote-submission and PayPal webhook flows in `001-python-backend-rewrite` (its FR-020–FR-022); those two conditions become examples of "error-or-higher" events under this feature's broader rule rather than a separate, narrower mechanism.
- Console output is standard output/error only; local log files, log rotation, and log retention policy are out of scope — the hosting platform is responsible for collecting and retaining what is written to standard output.
- Alert emails are delivered via the same outbound email mechanism already used for lead notifications in `001-python-backend-rewrite`, not a new/separate email provider.
- "Minimal" and "native" (from the feature description) are interpreted as a business constraint: no new third-party logging or alerting service is introduced, and the capability is built from what the server already has available (structured console output plus existing email delivery) rather than a hosted observability product.
- The operator alert inbox is the same designated business inbox already used for lead notifications and is a fixed configuration value, not user-editable through this feature.
- The cooldown window's exact duration is a tunable configuration value with a reasonable default (e.g., on the order of minutes); this feature does not fix it at a specific number, since the right value is an operational tuning decision rather than a business requirement.
- This capability is a cross-cutting concern usable by any current or future part of the server, not limited to the two flows that exist today.
