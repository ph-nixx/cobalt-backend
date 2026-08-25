# Quickstart: Validating the Rewritten Backend

Prerequisites: `uv` installed, Python 3.14 (per `.python-version`), access to a Neon/Postgres connection string for the *same* database the current system uses (per the confirmed data-continuity decision), and sandbox credentials for PayPal + SMTP.

## 1. Install & configure

```bash
uv sync
cp .env.example .env   # created during implementation; fill in DB_URL, PAYPAL_*, SMTP_*, FRONTEND_SHARED_SECRET, COBALT_EMAIL, PHONE_DISPLAY/PHONE_TEL, GOOGLE_ADS_ID, GA4_ID
```

Set `PAYPAL_ALLOW_SANDBOX=1` for the scenarios below that use sandbox event types.

## 2. Run the server

```bash
uv run python src/main.py
```

(There is no installed console-script entry point yet — the service is run directly.)

## 3. Validate User Story 1 — quote submission (see `contracts/quote-submission.md`)

**Happy path:**

```bash
curl -i -X POST http://localhost:8080/api/bookings \
  -H "Authorization: Bearer $FRONTEND_SHARED_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "service": "<a valid service option>",
    "vehicle": "<a valid vehicle option>",
    "pickup_time": "<ISO 8601 timestamp at least 1 hour from now>",
    "email": "test@example.com",
    "phone": "+15555550123"
  }'
```

Expected: `201`, a `quote_id` in the response, a lead-notification email arrives at `COBALT_EMAIL`'s configured inbox, and the row is visible in `quote_requests` shortly after (persistence is deferred/background — allow a moment).

**Rejected — no credential:**

Same request, omit the `Authorization` header → expect `401`, no email sent, no row written.

**Rejected — pickup too soon:**

Same request with `pickup_time` 5 minutes from now → expect `400`, no email sent, no row written.

**Degraded — invoice creation unavailable:**

Point `PAYPAL_*` config at an unreachable/invalid endpoint, repeat the happy path → expect `201` still, but the response has no `invoice_url` and the email references the quote id instead of an invoice link, and the response still returns within ~5s.

## 4. Validate User Story 2 — PayPal webhook (see `contracts/paypal-webhook.md`)

Use PayPal's sandbox webhook simulator (or a signed test payload) to send an `INVOICE.PAID` event whose `detail.reference` matches a `quote_requests.id` from step 3.

**Happy path:**

```bash
curl -i -X POST http://localhost:8080/api/hooks/paypal \
  -H "Content-Type: application/json" \
  -H "paypal-transmission-id: <from simulator>" \
  -H "paypal-transmission-time: <from simulator>" \
  -H "paypal-transmission-sig: <from simulator>" \
  -H "paypal-cert-url: <from simulator>" \
  -d @invoice-paid-event.json
```

Expected: `200`, exactly one new row in `conversions` with hashed (not raw) identifiers, and click ids present only if the quote's `first_click` is within 30 days.

**Idempotency:** resend the identical request → expect `200`, no new row (`conversions` count unchanged).

**Rejected signature:** corrupt `paypal-transmission-sig` and resend → expect `200` (per contract — never surfaced to caller), no new row, and an operator alert email should arrive (FR-020).

**No usable identifiers:** send an `INVOICE.PAID` event whose `detail.reference` matches no `quote_requests` row and whose invoice text contains no extractable phone → expect `200`, and an operator alert email should arrive (FR-021).

## 5. Side-by-side parity check (SC-007)

Before cutover, run the same set of inputs (valid submission, invalid submission, paid/duplicate/unverified webhook events) against both the current Next.js system and this rewrite, pointed at a shared staging database, and diff the outward behavior: HTTP status/validation messages, email content, and the resulting `quote_requests`/`conversions` rows.
