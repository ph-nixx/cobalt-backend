from asyncio import Lock
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import phonenumbers
from asyncpg import Pool
from httpx import AsyncClient
from pydantic import BaseModel, EmailStr, Field, HttpUrl, ValidationError
from pydantic_extra_types.phone_numbers import PhoneNumber
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from cfg import Settings
from emails import BookingLead, EmailNotSent

from . import logger

type InvoiceDraftURL = HttpUrl


class E164PhoneNumber(PhoneNumber):
    phone_format = "E164"


class PaypalToken(BaseModel):
    access_token: str
    expires_in: int
    expires_at: datetime | None = None


PAYPAL_TOKEN: PaypalToken = PaypalToken(access_token="", expires_in=0)
TOKEN_LOCK: Lock = Lock()
TOKEN_REFRESH_SKEW = timedelta(seconds=10)
DRAFT_TIMEOUT = 5.0  # seconds; mirrors legacy DRAFT_TIMEOUT_MS


class Submission(BaseModel):
    # this field shouldn't be sent by the client, it's server side meta data
    id: UUID = Field(default_factory=uuid4, init=False)

    # the only TS implementation allows the client to send the data and time seperately
    # this implementation forces the client to join them before sending
    name: str
    datetime: datetime
    email: EmailStr
    phone: E164PhoneNumber

    # the backend controls what form submission values are valid
    vehicle: Literal["suv", "sprinter"]
    service: Literal["airport", "corporate", "day-trip", "masters", "wedding", "other"]
    notes: str | None = None
    first_click: datetime | None = None
    gclid: str | None = None
    gbraid: str | None = None
    wbraid: str | None = None

    def as_row(self) -> tuple:
        return (
            self.id,
            self.email,
            self.phone,
            self.gclid,
            self.gbraid,
            self.wbraid,
            self.first_click,
        )


async def process_submission(request: Request) -> Response:
    try:
        submission = Submission.model_validate_json(await request.body())
    except ValidationError as e:
        return JSONResponse(
            {"invalid_fields": [err["loc"][0] for err in e.errors()]}, status_code=400
        )

    try:
        cfg: Settings = request.state.cfg
        await request.state.gmail.send(
            BookingLead(
                **submission.model_dump(),
                sender=cfg.SMTP_USER,
                recipient=cfg.SMTP_USER,
                invoice_url=await create_invoice_draft(submission, request),
            )
        )
    except EmailNotSent:
        return JSONResponse({}, status_code=500)

    return Response(
        status_code=200,
        background=BackgroundTask(persist_submission, submission, request.state.db),
    )


async def persist_submission(submission: Submission, db: Pool):
    await db.execute(
        """
        INSERT INTO quote_requests (
            id, 
            email, 
            phone, 
            gclid, 
            gbraid, 
            wbraid, 
            first_click
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        *submission.as_row(),
    )


async def create_invoice_draft(
    submission: Submission, request: Request
) -> InvoiceDraftURL | None:
    client: AsyncClient = request.state.httpx
    cfg: Settings = request.state.cfg
    async with TOKEN_LOCK:
        if PAYPAL_TOKEN.expires_at is None or PAYPAL_TOKEN.expires_at <= datetime.now(
            UTC
        ):
            try:
                resp = await client.post(
                    cfg.PAYPAL_OAUTH_URL,
                    headers={
                        "Authorization": f"Basic {cfg.PAYPAL_CREDS}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    data={"grant_type": "client_credentials"},
                    timeout=DRAFT_TIMEOUT,
                )
                resp.raise_for_status()
                body = PaypalToken.model_validate_json(await resp.aread())
            except Exception as e:
                logger.error(
                    "PayPal token refresh failed: %s",
                    e,
                    extra={"error_type": type(e).__name__},
                )
                return None

            PAYPAL_TOKEN.expires_at = (
                datetime.now(UTC)
                + timedelta(seconds=body.expires_in)
                - TOKEN_REFRESH_SKEW
            )
            PAYPAL_TOKEN.access_token = body.access_token

    phone = []
    try:
        parsed_phone = phonenumbers.parse(submission.phone, None)
        phone = [
            {
                "country_code": str(parsed_phone.country_code),
                "national_number": str(parsed_phone.national_number),
                "phone_type": "MOBILE",
            }
        ]
    except Exception:
        pass

    try:
        resp = await client.post(
            cfg.PAYPAL_INVOICE_DRAFT_URL,
            headers={
                "Authorization": f"Bearer {PAYPAL_TOKEN.access_token}",
            },
            timeout=DRAFT_TIMEOUT,
            json={
                "detail": {
                    "currency_code": "USD",
                    "invoice_date": date.today().isoformat(),
                    "reference": str(submission.id),
                },
                "primary_recipients": [
                    {
                        "billing_info": {
                            "email_address": submission.email,
                            "phones": phone,
                        },
                    },
                ],
                "items": [
                    {
                        "name": "BOOKING DRAFT",
                        "quantity": "1",
                        "unit_amount": {"currency_code": "USD", "value": "0.00"},
                    },
                ],
            },
        )
        resp.raise_for_status()
        result: dict[str, str] = resp.json()
    except Exception as e:
        logger.error(
            "PayPal create invoice draft failed for submission %s: %s",
            submission.id,
            e,
            extra={"error_type": type(e).__name__, "submission_id": str(submission.id)},
        )
        return None

    draft_id = result.get("id")
    href: str | None = result.get("href") or resp.headers.get("location")
    if draft_id is not None:
        return HttpUrl(url=_draft_url(draft_id))
    elif href is not None:
        parts = href.rsplit("/", 1)
        if len(parts) != 2:
            return None
        return HttpUrl(url=_draft_url(parts[-1]))

    return None


def _draft_url(draft_id: str) -> str:
    return f"https://www.paypal.com/invoice/s/{draft_id}/edit"
