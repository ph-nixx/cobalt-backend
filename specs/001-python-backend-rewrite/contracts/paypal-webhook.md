# Contract: PayPal Webhook

`POST /api/paypal-webhook`

Replaces the current `app/api/paypal-webhook/route.ts` handler (spec User Story 2). This endpoint is intentionally **public** — PayPal must be able to reach it from the internet — which is why its protection is signature verification (FR-008/FR-009), not the shared-secret scheme used for the quote-submission endpoint (see `research.md` #3).

## Request

```
Content-Type: application/json
Headers:
  paypal-transmission-id
  paypal-transmission-time
  paypal-transmission-sig
  paypal-cert-url
```

Body: a PayPal webhook event payload (arbitrary shape by event type; only `INVOICE.PAID`, and in sandbox mode `INVOICE.SENT`, are acted on — FR-010).

## Processing order (all before any DB write)

1. **Authenticity verification** (FR-008): `paypal-cert-url` must be `https://` on `*.paypal.com`; fetch the cert; RSA/SHA-256-verify `transmission-id|transmission-time|webhook-id|crc32(body)` against the configured webhook id(s).
   - Failure → reject without processing (FR-009), **and** trigger the operator alert (FR-020). Response is still `200 OK` (see below) — the failure is invisible to the caller by design, since a webhook is not a trusted client to signal internal state to.
2. **Event-type filter** (FR-010): only `INVOICE.PAID` (plus `INVOICE.SENT` in sandbox mode) proceeds; anything else is acknowledged with no further processing.
3. **Lead re-join** (FR-011): look up `quote_requests` by the invoice's reference.
4. **Click-id staleness gate** (FR-012).
5. **Identifier resolution + hashing** (FR-013, FR-014); if resolution yields nothing usable at all, trigger the operator alert (FR-021).
6. **Idempotent write** (FR-015): `INSERT ... ON CONFLICT (txn) DO NOTHING` into `conversions`.

## Response

**Always `200 OK`** (FR-016), regardless of:
- signature verification outcome,
- event type,
- whether a matching Quote Request was found,
- whether the database write succeeded.

```json
{ "received": true }
```

This is deliberate: PayPal must never see a failure status for a well-formed delivery, or it will retry-storm the endpoint. Anything the operator needs to know is routed through FR-020/FR-021's alerting instead of the HTTP response (see `research.md` #4).

## Out of scope for this contract

- Any response body distinguishing *why* an event was a no-op (event-type filter vs. no quote match vs. stale click ids) — none of these are observable to the caller by design.
