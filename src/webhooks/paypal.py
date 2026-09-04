import base64
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal
from zlib import crc32

from asyncpg import Pool
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from pydantic import (
    AfterValidator,
    BaseModel,
    EmailStr,
    Field,
    HttpUrl,
    ValidationError,
)
from starlette.requests import Request
from starlette.responses import Response

from bookings import Submission

# from . import logger


class PaypalEvent(BaseModel):
    class Resource(BaseModel):
        class Detail(BaseModel):
            invoice_number: str
            reference: str | None = None

        class Amount(BaseModel):
            currency_code: str
            value: float

        class PrimaryRecipient(BaseModel):
            class BillingInfo(BaseModel):
                email_address: EmailStr

            billing_info: BillingInfo

        id: str
        status: Literal["PAID"]
        detail: Detail
        amount: Amount
        primary_recipients: list[PrimaryRecipient] | None = None

    id: str
    create_time: datetime
    resource_type: Literal["invoicing"]
    event_type: Literal["INVOICING.INVOICE.PAID"]
    resource: Resource


class Conversion(Submission):
    def has_ads_identifier(self) -> bool:
        return (
            self.gclid is not None or self.gbraid is not None or self.wbraid is not None
        )

    def _as_row(self, event: PaypalEvent) -> tuple:
        recipients = event.resource.primary_recipients
        email = recipients[0].billing_info.email_address if recipients else self.email
        return (
            event.id,
            self.id,
            self.gclid,
            self.gbraid,
            self.wbraid,
            sha256(email.encode()).hexdigest(),
            sha256(self.phone.encode()).hexdigest(),
            event.resource.amount.value,
            event.resource.amount.currency_code,
            event.create_time,
        )


_VALID_PAYPAL_CERT_HOSTS = frozenset(
    {
        "api.paypal.com",
        "api-m.paypal.com",
        "api.sandbox.paypal.com",
        "api-m.sandbox.paypal.com",
    }
)


def validate_paypal_host(value: HttpUrl) -> HttpUrl:
    if value.scheme == "https" and value.host in _VALID_PAYPAL_CERT_HOSTS:
        return value

    raise ValueError("untrusted paypal-cert-url")


class PaypalAuthHeaders(BaseModel):
    # ASGI spec ensures that all header field names are lower case normalized
    id: str = Field(alias="paypal-transmission-id")
    time: str = Field(alias="paypal-transmission-time")
    url: Annotated[
        HttpUrl, Field(alias="paypal-cert-url"), AfterValidator(validate_paypal_host)
    ]
    signature: str = Field(alias="paypal-transmission-sig")


async def record_payed_invoice(request: Request) -> Response:
    event = await _webhook_auth_protocol(request)
    if isinstance(event, Response):
        return event

    if event.resource.detail.reference is None:
        return Response(status_code=200)

    db: Pool = request.state.db
    row = await db.fetchrow(
        """
        SELECT id, email, phone, gclid, gbraid, wbraid, first_click
        FROM quote_requests
        WHERE id = $1
        """,
        event.resource.detail.reference,
    )
    if row is None:
        return Response(status_code=200)

    conversion = Conversion.model_validate(dict(row))
    if not conversion.has_ads_identifier():
        return Response(status_code=200)

    await db.execute(
        """
        INSERT INTO conversions
            (txn, quote_id, gclid, gbraid, wbraid, email_sha256, phone_sha256, conversion_value, currency, conversion_time)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (txn) DO NOTHING
        """,
        *conversion._as_row(event),
    )

    return Response(status_code=200)


async def _webhook_auth_protocol(request: Request) -> PaypalEvent | Response:
    """Run the proprietary Paypal auth flow on a incoming POST request"""
    try:
        headers = PaypalAuthHeaders.model_validate(request.headers)
    except ValidationError:
        return Response(
            "Invalid paypal request header schema or values", status_code=400
        )

    cert_req = await request.state.httpx.get(str(headers.url))
    if cert_req.is_error:
        return Response("Internal HTTP request failure", status_code=500)

    cert_bytes = await cert_req.aread()
    cert = x509.load_pem_x509_certificate(cert_bytes)
    body = await request.body()
    crc = crc32(body)
    try:
        cert.public_key().verify(
            signature=base64.b64decode(headers.signature),
            data=f"{headers.id}|{headers.time}|{request.state.cfg.PAYPAL_WEBHOOK_ID}|{crc}".encode(),
            padding=padding.PKCS1v15(),
            algorithm=hashes.SHA256(),
        )
    except InvalidSignature:
        return Response(
            "Content hash did not match expected signature", status_code=400
        )

    try:
        return PaypalEvent.model_validate_json(body)
    except ValidationError:
        return Response("Invalid JSON schema", status_code=400)
