import asyncio
from datetime import UTC, datetime

import httpx
import phonenumbers
import pytest
from pydantic import BaseModel
from pytest_httpx import HTTPXMock
from starlette.requests import Request

from cfg import Settings

from . import submission


def _cfg() -> Settings:
    return Settings(
        PAYPAL_WEBHOOK_ID="DEFAULT",
        PAYPAL_CREDS="test-creds",
        PAYPAL_SANDBOX_CREDS="test-sandbox-creds",
        PG_URL="",
        SMTP_USER="",
        SMTP_PASSWORD="",
    )


def _sandbox_cfg() -> Settings:
    """Builds Settings pointed at PayPal sandbox URLs, with creds from PAYPAL_SANDBOX_CREDS."""
    cfg = Settings(
        PAYPAL_WEBHOOK_ID="SANDBOX",
        PAYPAL_CREDS="placeholder",
        PAYPAL_OAUTH_URL="https://api-m.sandbox.paypal.com/v1/oauth2/token",
        PAYPAL_INVOICE_DRAFT_URL="https://api-m.sandbox.paypal.com/v2/invoicing/invoices",
        PG_URL="",
        SMTP_USER="",
        SMTP_PASSWORD="",
        PAYPAL_SANDBOX_CREDS="",
    )
    cfg.PAYPAL_CREDS = cfg.PAYPAL_SANDBOX_CREDS
    return cfg


def _draft_id_from_url(url) -> str:
    """Pulls the invoice id out of a https://www.paypal.com/invoice/s/{id}/edit draft URL."""
    return str(url).rstrip("/").split("/")[-2]


def _submission() -> submission.Submission:
    return submission.Submission(
        name="Test Rider",
        datetime=datetime.now(UTC),
        email="rider@example.com",
        phone="+14155552671",
        vehicle="suv",
        service="airport",
    )


async def test_paypal_token_is_only_refreshed_once(httpx_mock: HTTPXMock):
    """
    When PAYPAL_TOKEN expires, only one Coroutine refreshes it until it expires again.

    * We are mainly testing for a race
    """
    cfg = _cfg()
    submission.PAYPAL_TOKEN = submission.PaypalToken(
        access_token="", expires_in=0, expires_at=None
    )
    submission.TOKEN_LOCK = asyncio.Lock()

    new_token = "fresh"

    async def oauth_response(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return httpx.Response(200, json={"access_token": new_token, "expires_in": 3600})

    httpx_mock.add_callback(oauth_response, url=cfg.PAYPAL_OAUTH_URL)
    httpx_mock.add_response(
        url=cfg.PAYPAL_INVOICE_DRAFT_URL,
        json={"id": "INV-1"},
        is_reusable=True,
    )

    async with httpx.AsyncClient() as client:
        request = Request({"type": "http", "state": {"httpx": client, "cfg": cfg}})
        sub = _submission()

        results = await asyncio.gather(
            *(submission.create_invoice_draft(sub, request) for _ in range(3))
        )

    assert len(httpx_mock.get_requests(url=cfg.PAYPAL_OAUTH_URL)) == 1
    assert all(result is not None for result in results)
    assert submission.PAYPAL_TOKEN.access_token == new_token


class FakeDBPool:
    """Records executed queries in place of a real asyncpg.Pool."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, query: str, *args) -> None:
        """Captures the query/args instead of touching a real database."""
        self.calls.append((query, args))


class FakeGmail:
    """Records sent emails in place of a real Gmail/SMTP client."""

    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, email) -> None:
        """Captures the email instead of sending over SMTP."""
        self.sent.append(email)


class BillingInfo(BaseModel):
    email_address: str | None = None
    phones: list[dict] = []


class PrimaryRecipient(BaseModel):
    billing_info: BillingInfo | None = None


class InvoiceDetail(BaseModel):
    reference: str | None = None
    currency_code: str | None = None
    invoice_date: str | None = None


class PaypalInvoice(BaseModel):
    id: str
    status: str
    detail: InvoiceDetail | None = None
    primary_recipients: list[PrimaryRecipient] = []


class PaypalSandboxClient:
    """Thin async wrapper over the PayPal sandbox invoicing API, used only for assertions/cleanup."""

    _OAUTH_URL = "https://api-m.sandbox.paypal.com/v1/oauth2/token"
    _INVOICES_URL = "https://api-m.sandbox.paypal.com/v2/invoicing/invoices"

    def __init__(self, client: httpx.AsyncClient, creds: str) -> None:
        self._client = client
        self._creds = creds
        self._access_token: str | None = None

    async def _token(self) -> str:
        """Fetches (and caches) an OAuth client-credentials token for the sandbox."""
        if self._access_token is not None:
            return self._access_token

        resp = await self._client.post(
            self._OAUTH_URL,
            headers={
                "Authorization": f"Basic {self._creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
        )
        resp.raise_for_status()
        self._access_token = resp.json()["access_token"]
        return self._access_token

    async def get_invoice(self, invoice_id: str) -> PaypalInvoice:
        """Fetches a single invoice by id."""
        token = await self._token()
        resp = await self._client.get(
            f"{self._INVOICES_URL}/{invoice_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return PaypalInvoice.model_validate(resp.json())

    async def delete_draft(self, invoice_id: str) -> None:
        """Best-effort deletes a DRAFT invoice; never raises."""
        try:
            token = await self._token()
            resp = await self._client.delete(
                f"{self._INVOICES_URL}/{invoice_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
        except Exception:
            pass


@pytest.mark.e2e
async def test_create_invoice_draft_extracts_submission_data():
    """Creates a real sandbox draft, then asserts the extracted phone/email/reference match the submission."""
    cfg = _sandbox_cfg()
    submission.PAYPAL_TOKEN = submission.PaypalToken(
        access_token="", expires_in=0, expires_at=None
    )
    submission.TOKEN_LOCK = asyncio.Lock()
    sub = _submission()

    async with httpx.AsyncClient() as client:
        request = Request({"type": "http", "state": {"httpx": client, "cfg": cfg}})
        draft_url = await submission.create_invoice_draft(sub, request)
        assert draft_url is not None

        draft_id = _draft_id_from_url(draft_url)
        paypal_client = PaypalSandboxClient(client, cfg.PAYPAL_CREDS)
        try:
            invoice = await paypal_client.get_invoice(draft_id)

            assert invoice.detail is not None
            assert invoice.detail.reference == str(sub.id)

            billing_info = invoice.primary_recipients[0].billing_info
            assert billing_info is not None
            assert billing_info.email_address == sub.email

            parsed_phone = phonenumbers.parse(str(sub.phone), None)
            assert billing_info.phones == [
                {
                    "country_code": str(parsed_phone.country_code),
                    "national_number": str(parsed_phone.national_number),
                    "phone_type": "MOBILE",
                }
            ]
        finally:
            await paypal_client.delete_draft(draft_id)


@pytest.mark.e2e
async def test_persist_submission_writes_expected_row():
    """Asserts persist_submission hands the fake db pool the exact row fields from the submission."""
    fake_db = FakeDBPool()
    sub = _submission()

    await submission.persist_submission(sub, fake_db)

    assert len(fake_db.calls) == 1
    _, args = fake_db.calls[0]
    assert args == sub.as_row()


@pytest.mark.e2e
async def test_process_submission_end_to_end():
    """Runs the full endpoint: real draft creation, fake persist, fake email, then verifies + cleans up."""
    cfg = _sandbox_cfg()
    submission.PAYPAL_TOKEN = submission.PaypalToken(
        access_token="", expires_in=0, expires_at=None
    )
    submission.TOKEN_LOCK = asyncio.Lock()

    fake_db = FakeDBPool()
    fake_gmail = FakeGmail()
    sub = _submission()
    body = sub.model_dump_json(exclude={"id"}).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async with httpx.AsyncClient() as client:
        request = Request(
            {
                "type": "http",
                "state": {
                    "cfg": cfg,
                    "httpx": client,
                    "db": fake_db,
                    "gmail": fake_gmail,
                },
            },
            receive=receive,
        )

        response = await submission.process_submission(request)
        assert response.status_code == 200
        assert response.background is not None
        await response.background()

        assert len(fake_gmail.sent) == 1
        sent_email = fake_gmail.sent[0]
        assert sent_email.invoice_url is not None

        assert len(fake_db.calls) == 1
        _, args = fake_db.calls[0]
        persisted_id, persisted_email = args[0], args[1]
        assert persisted_email == sub.email

        draft_id = _draft_id_from_url(sent_email.invoice_url)
        paypal_client = PaypalSandboxClient(client, cfg.PAYPAL_CREDS)
        try:
            invoice = await paypal_client.get_invoice(draft_id)
            assert invoice.detail is not None
            assert invoice.detail.reference == str(persisted_id)
        finally:
            await paypal_client.delete_draft(draft_id)
