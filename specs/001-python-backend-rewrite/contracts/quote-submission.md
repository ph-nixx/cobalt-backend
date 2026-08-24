# Contract: Quote Submission

`POST /api/quotes`

Replaces the current `submitContactRequest` Server Action (spec User Story 1). Unlike the Server Action, this is a directly-callable HTTP endpoint, which is precisely why FR-001a exists — see **Access control** below.

## Access control (FR-001a)

Every request MUST include the shared-secret credential the frontend is configured with (see `research.md` #3), e.g.:

```
Authorization: Bearer <shared-secret>
```

- Missing or incorrect credential → `401 Unauthorized`, no validation, no side effects (FR-001a, SC-009).
- This check runs **before** body validation.

## Request

```
Content-Type: application/json
```

| Field | Type | Required | Rule |
|---|---|---|---|
| `service` | string (enum) | yes | Must be one of the recognized service options (FR-017). |
| `vehicle` | string (enum) | yes | Must be one of the recognized vehicle options (FR-017). |
| `pickup_time` | string (ISO 8601, timezone-aware) | yes | Must be ≥ 1 hour from the time the request is received. |
| `email` | string | yes | Valid email address. |
| `phone` | string | yes | Must normalize to E.164. |
| `gclid` | string | no | Ad-click identifier, if present on the client. |
| `gbraid` | string | no | Ad-click identifier, if present on the client. |
| `wbraid` | string | no | Ad-click identifier, if present on the client. |
| `first_click` | string (ISO 8601), nullable | no | Timestamp of the customer's first tracked visit, if known. |

## Responses

**`201 Created`** — submission accepted (FR-002 negative case is the mirror of this).

```json
{
  "quote_id": "<uuid>",
  "invoice_url": "<string, present only if invoice creation succeeded within the bound>"
}
```

- Returned once the lead-notification email has been sent successfully (FR-006 — email failure fails the whole request, so a `201` guarantees the email went out).
- `invoice_url` is omitted (not null — absent) when Draft Invoice creation didn't complete within the bound (FR-004); this is not an error condition for the response as a whole.
- Quote persistence (FR-007) happens after this response is returned and does not affect its content or timing.

**`400 Bad Request`** — validation failed (FR-001, FR-002).

```json
{
  "errors": [
    { "field": "pickup_time", "message": "<human-readable reason>" }
  ]
}
```

- No lead, invoice, or notification is created.

**`401 Unauthorized`** — request did not carry a valid frontend credential (FR-001a).

- Body content is intentionally minimal/generic; must not help an attacker distinguish "wrong secret" from "malformed request."

**`500 Internal Server Error`** — the lead-notification email could not be sent (FR-006).

- The customer must be informed the submission failed (this is the one failure mode in this flow that is NOT swallowed, per FR-006).

## Out of scope for this contract

- Rate limiting / abuse protection — explicitly out of scope (spec Clarifications).
- Idempotency of duplicate submissions from the same customer double-clicking — not specified; no dedup behavior is contracted here (see spec Completion Report's "Outstanding" note).
