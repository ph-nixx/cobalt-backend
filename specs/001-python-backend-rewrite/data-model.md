# Phase 1 Data Model: Lead Capture & Conversion Backend Rewrite

Per the confirmed data-continuity decision (see `spec.md` Clarifications), this rewrite reads and writes the **same existing** `quote_requests` and `conversions` tables the current Next.js system uses — it does not introduce a new schema. The shapes below are reconstructed from the feature description and the functional requirements; they describe the logical fields this rewrite depends on, not a new DDL to run.

> **Before implementation**: introspect the live table definitions (e.g., `information_schema.columns`, or the source system's existing migration/schema file if one is checked in elsewhere) to confirm exact column names, types, and nullability rather than assuming the shapes below. Treat this document as the logical contract the rewrite's code depends on; reconcile against the physical schema as an early implementation task.

## Quote Request

Represents a prospective customer's submitted lead (spec: Key Entities > Quote Request; FR-003, FR-007).

| Field | Type (logical) | Notes |
|---|---|---|
| `id` | UUID | Primary key. This is the "reference" a Draft Invoice carries and the paid-invoice webhook uses to rejoin the lead (FR-011). |
| `email` | text | Contact email captured at submission. |
| `phone` | text | Contact phone, normalized to E.164 (FR-001). |
| `gclid` | text, nullable | Google Ads click identifier, if present at submission. |
| `gbraid` | text, nullable | iOS app-to-web click identifier, if present. |
| `wbraid` | text, nullable | Web-to-app click identifier, if present. |
| `first_click` | timestamptz, nullable | Timestamp of the customer's first tracked visit; drives the 30-day click-id staleness gate (FR-012). |
| `created_at` | timestamptz | Submission time. |

**Validation rules** (enforced before a row is written, per FR-001/FR-001a):
- Must originate from the authorized frontend client (FR-001a).
- `email`/`phone` must pass the authoritative validation rules (recognized format; phone normalizable to E.164).
- Implicit pickup-time rule (≥1 hour out) is enforced at submission time as a request-level check; it does not need to be persisted as a Quote Request field per the current system's field list (FR-007 lists exactly `id, email, phone, gclid, gbraid, wbraid, first_click`).

**Lifecycle**: write-once at submission (FR-007); read once, at most, when a matching paid-invoice webhook arrives (FR-011). No update-in-place; no deletion behavior specified.

## Draft Invoice

Not a locally-persisted entity — it is a resource created in the payment provider's system (spec: Key Entities > Draft Invoice; FR-004). The rewrite's own state footprint for it is just the invoice reference/id returned at creation time, used only transiently to build the lead-notification email's invoice link (FR-005). The provider-side invoice carries the Quote Request's `id` as its own reference field, which is what makes the later webhook rejoin (FR-011) possible — no local table needed for this entity.

## Conversion Entry

Represents the idempotent record written when an invoice is paid (spec: Key Entities > Conversion Entry; FR-012–FR-016).

| Field | Type (logical) | Notes |
|---|---|---|
| `txn` | text | Payment transaction id. **Unique** — the idempotency key (FR-015): write is `INSERT ... ON CONFLICT (txn) DO NOTHING`. |
| `email_hash` | text, nullable | One-way hashed email (FR-014), present only if an email was resolvable. |
| `phone_hash` | text, nullable | One-way hashed phone (FR-014), present only if a phone was resolvable. |
| `gclid` | text, nullable | Carried over from the matched Quote Request **only if** still fresh (FR-012). |
| `gbraid` | text, nullable | Same freshness rule as `gclid`. |
| `wbraid` | text, nullable | Same freshness rule as `gclid`. |
| `created_at` | timestamptz | Conversion write time. |

**Validation rules**:
- Never contains raw (unhashed) email/phone (FR-014, SC-005).
- Click identifiers are omitted (not just null-but-present — genuinely excluded from consideration) when `first_click` is missing or more than 30 days before the payment notification (FR-012).
- At most one row per `txn` ever exists (FR-015).

**Lifecycle**: write-once, created only in response to a verified "invoice paid" (or sandbox "invoice sent") notification (FR-008–FR-010). Never updated or deleted by this system.

## Lead Notification

Not persisted — it is the business-facing email generated synchronously per accepted submission (spec: Key Entities > Lead Notification; FR-005, FR-006). Modeled here only to name its required content, since FR-005 is testable against it:

| Field | Source |
|---|---|
| Customer contact details | From the validated submission. |
| Formatted pickup time | Submission's pickup time, formatted in the business's local time zone (FR-018). |
| Invoice link **or** plain reference | Invoice link if Draft Invoice creation succeeded within the bound (FR-004); otherwise a plain reference to the Quote Request `id`. |

## Operator Alert (new in this rewrite — see Clarifications)

Not persisted — a second, narrowly-scoped email (research.md #4) sent only for the two conditions FR-020/FR-021 name:

| Condition | Content |
|---|---|
| Webhook authenticity-verification failure (FR-020) | That verification failed; enough context to investigate (timestamp, which check failed) without including the unverified payload's sensitive contents. |
| Conversion recorded with no usable contact identifiers (FR-021) | The `txn`, and that identifier resolution came up fully empty. |

## Relationships

```
Quote Request (id) ──── carried as reference on ────> Draft Invoice (provider-side)
Quote Request (id) <──── matched via reference on ──── paid-invoice webhook notification
Quote Request  ────produces (0 or 1)───> Conversion Entry   [linked implicitly via the webhook's invoice reference, not a stored foreign key per the current field list]
```

No entity in this model is ever updated after creation; both persisted entities (Quote Request, Conversion Entry) are append-only, which is what makes the idempotency and "never blocks the customer-facing response" requirements (FR-007, FR-015, FR-016) straightforward to satisfy.
