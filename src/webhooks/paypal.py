import base64
from datetime import datetime
from zlib import crc32

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from pydantic import BaseModel, Field, HttpUrl, ValidationError
from starlette.requests import Request
from starlette.responses import Response

client = httpx.AsyncClient()


class PaypalEvent(BaseModel):
    class Resource(BaseModel):
        class Invoice(BaseModel):
            class Detail(BaseModel):
                reference: str

            class Amount(BaseModel):
                currency_code: str
                value: float

            class Recipient(BaseModel):
                class BillingInfo(BaseModel):
                    email_address: str

                billing_info: BillingInfo

            class Item(BaseModel):
                name: str
                description: str

            id: str
            detail: Detail
            amount: Amount
            primary_recipients: list[Recipient]
            items: list[Item]

        invoice: Invoice

    create_time: datetime
    resource: Resource


class PaypalAuthHeaders(BaseModel):
    # ASGI spec ensures that all header field names are lower case normalized
    id: str = Field(alias="paypal-transmission-id")
    time: str = Field(alias="paypal-transmission-time")
    url: HttpUrl = Field(alias="paypal-cert-url")
    signature: str = Field(alias="paypal-transmission-sig")


async def record_payed_invoice(request: Request) -> Response:
    event = await _request_auth_protocol(request)
    if isinstance(event, Response):
        return event

    return Response(event.model_dump_json(), status_code=200)


async def _request_auth_protocol(request: Request) -> PaypalEvent | Response:
    """Run the proprietary Paypal auth flow on a incoming POST request"""
    try:
        headers = PaypalAuthHeaders.model_validate(request.headers)
    except ValidationError:
        return Response(
            "Invalid paypal request header schema or values", status_code=400
        )

    cert_req = await client.get(str(headers.url))
    if cert_req.is_error:
        return Response("Internal HTTP request failure", status_code=500)

    cert_bytes = await cert_req.aread()
    cert = x509.load_pem_x509_certificate(cert_bytes)
    body = await request.body()
    crc = crc32(body)
    try:
        cert.public_key().verify(
            signature=base64.b64decode(headers.signature),
            data=f"{headers.id}|{headers.time}|{request.app.state.cfg.PAYPAL_WEBHOOK_ID}|{crc}".encode(),
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
