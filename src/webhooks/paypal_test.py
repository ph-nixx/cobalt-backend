import base64
import json
from datetime import UTC, datetime, timedelta
from zlib import crc32

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.x509.oid import NameOID
from pytest_httpx import HTTPXMock
from starlette.requests import Request
from starlette.responses import Response

from cfg import Settings

from .paypal import PaypalEvent, _request_auth_protocol

CERT_URL = "https://api.sandbox.paypal.com/fake-cert.pem"

VALID_EVENT_PAYLOAD = {
    "id": "WH-2WR32451HC0233532-1TR54305UM875670F",
    "create_time": "2026-08-24T22:12:06Z",
    "resource_type": "invoicing",
    "event_type": "INVOICING.INVOICE.PAID",
    "summary": "An invoice was paid",
    "resource_version": "2.0",
    "resource": {
        "id": "INV2-XXXX-XXXX-XXXX-XXXX",
        "status": "PAID",
        "detail": {"invoice_number": "0001", "invoice_date": "2026-08-24"},
        "amount": {"currency_code": "USD", "value": "250.00"},
        "due_amount": {"currency_code": "USD", "value": "0.00"},
        "payments": {
            "paid_amount": {"currency_code": "USD", "value": "250.00"},
            "transactions": [
                {
                    "type": "PAYPAL",
                    "payment_id": "TXNID12345",
                    "payment_date": "2026-08-24",
                    "method": "PAYPAL",
                    "amount": {"currency_code": "USD", "value": "250.00"},
                }
            ],
        },
    },
    "links": [
        {
            "href": "https://api-m.paypal.com/v2/invoicing/invoices/INV2-XXXX-XXXX-XXXX-XXXX",
            "rel": "self",
            "method": "GET",
        }
    ],
}

MALFORMED_EVENT_PAYLOAD = {
    "id": "WH-2WR32451HC0233532-1TR54305UM875670F",
    "create_time": "2026-08-24T22:12:06Z",
    "resource_type": "invoicing",
    "event_type": "INVOICING.INVOICE.PAID",
    "summary": "An invoice was paid",
    "resource_version": "2.0",
    "resource": {
        "id": "INV2-XXXX-XXXX-XXXX-XXXX",
        "status": "PAID",
        "detail": {"invoice_number": "0001", "invoice_date": "2026-08-24"},
        "amount": {"currency_code": "USD", "value": "not-a-number"},
        "due_amount": {"currency_code": "USD", "value": "0.00"},
        "payments": {
            "paid_amount": {"currency_code": "USD", "value": "250.00"},
            "transactions": [
                {
                    "type": "PAYPAL",
                    "payment_id": "TXNID12345",
                    "payment_date": "2026-08-24",
                    "method": "PAYPAL",
                    "amount": {"currency_code": "USD", "value": "250.00"},
                }
            ],
        },
    },
    "links": [
        {
            "href": "https://api-m.paypal.com/v2/invoicing/invoices/INV2-XXXX-XXXX-XXXX-XXXX",
            "rel": "self",
            "method": "GET",
        }
    ],
}


@pytest.fixture(scope="module")
def cfg() -> Settings:
    return Settings(
        PAYPAL_WEBHOOK_ID="DEFAULT", PG_URL="", SMPT_USER="", SMPT_PASSWORD=""
    )


@pytest.fixture(scope="module")
def rsa_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def cert_pem(rsa_key: RSAPrivateKey) -> bytes:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "paypal-test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(rsa_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(rsa_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def build_paypal_request(
    body: bytes,
    rsa_key: RSAPrivateKey,
    cfg: Settings,
    *,
    headers: dict | None = None,
) -> Request:
    if headers is None:
        transmission_id = "11111111-2222-3333-4444-555555555555"
        transmission_time = "2026-08-24T12:00:00Z"
        crc = crc32(body)
        message = f"{transmission_id}|{transmission_time}|{cfg.PAYPAL_WEBHOOK_ID}|{crc}"
        signature = rsa_key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
        signature = base64.b64encode(signature).decode()

        headers = {
            "paypal-transmission-id": transmission_id,
            "paypal-transmission-time": transmission_time,
            "paypal-cert-url": CERT_URL,
            "paypal-transmission-sig": signature,
        }

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        "state": {"cfg": cfg},
    }

    sent = False

    async def receive() -> dict:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


async def test_valid_payload_is_accepted(
    cfg: Settings, httpx_mock: HTTPXMock, rsa_key: RSAPrivateKey, cert_pem: bytes
):
    httpx_mock.add_response(url=CERT_URL, content=cert_pem)
    req = build_paypal_request(
        json.dumps(VALID_EVENT_PAYLOAD).encode(),
        rsa_key,
        cfg,
    )

    response = await _request_auth_protocol(req)
    assert isinstance(response, PaypalEvent), "Valid HTTP request failed"


async def test_bad_headers_return_400(
    cfg: Settings, httpx_mock: HTTPXMock, rsa_key: RSAPrivateKey, cert_pem: bytes
):
    httpx_mock.add_response(url=CERT_URL, content=cert_pem)
    body = json.dumps(VALID_EVENT_PAYLOAD).encode()
    req = build_paypal_request(
        body,
        rsa_key,
        cfg,
        headers={
            "paypal-transmission-id": "11111111-2222-3333-4444-555555555555",
            "paypal-cert-url": CERT_URL,
            "paypal-transmission-sig": "unused",
        },
    )
    response = await _request_auth_protocol(req)
    assert isinstance(response, Response)
    assert 400 <= response.status_code < 500, "Missing headers didn't return a 4xx"

    req = build_paypal_request(
        body,
        rsa_key,
        cfg,
        headers={
            "paypal-transmission-id": "11111111-2222-3333-4444-555555555555",
            "paypal-transmission-time": "08-20-2006",
            "paypal-cert-url": CERT_URL,
            "paypal-transmission-sig": "fuck",
        },
    )
    response = await _request_auth_protocol(req)
    assert isinstance(response, Response)
    assert 400 <= response.status_code < 500, "Bad signature didn't return a 4xx"


async def test_malformed_json_schema_returns_400(
    cfg: Settings, httpx_mock: HTTPXMock, rsa_key: RSAPrivateKey, cert_pem: bytes
):
    httpx_mock.add_response(url=CERT_URL, content=cert_pem)
    req = build_paypal_request(
        json.dumps(MALFORMED_EVENT_PAYLOAD).encode(),
        rsa_key,
        cfg,
    )

    response = await _request_auth_protocol(req)
    assert isinstance(response, Response)
    assert 400 <= response.status_code < 500, "Bad JSON schema didn't return a 4xx"
