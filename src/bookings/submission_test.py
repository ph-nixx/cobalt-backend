import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel
from pytest_httpx import HTTPXMock
from starlette.requests import Request

from cfg import Settings
from emails import BookingLead
from webhooks.paypal import Detail

from . import submission as s
from .submission import Submission

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env.local"


class DraftCleanupFailure(Exception):
    pass


@pytest.fixture
def cfg() -> Settings:
    return Settings(
        PAYPAL_WEBHOOK_ID="DEFAULT",
        PAYPAL_CREDS="test-creds",
        PG_URL="",
        SMTP_USER="",
        SMTP_PASSWORD="",
        COBALT_GMAIL="",
    )


@pytest.fixture
def sandbox_cfg() -> Settings:
    """Builds Settings pointed at PayPal sandbox URLs, with creds/pg/smtp read from .env.local."""
    return Settings(_env_file=_ENV_FILE)


@pytest.fixture
def user_submission() -> dict:
    """A raw payload shaped like what a client actually submits (no server-assigned id)."""
    return {
        "name": "Test Rider",
        "datetime": datetime.now(UTC).isoformat(),
        "email": "rider@example.com",
        "phone": "+14155552671",
        "vehicle": "suv",
        "service": "airport",
    }


async def test_paypal_token_is_only_refreshed_once(
    httpx_mock: HTTPXMock, cfg: Settings, user_submission: dict
):
    """
    When PAYPAL_TOKEN expires, only one Coroutine refreshes it until it expires again.

    * We are mainly testing for a race
    """
    s.PAYPAL_TOKEN = s.PaypalToken(access_token="", expires_in=0, expires_at=None)
    s.TOKEN_LOCK = asyncio.Lock()

    new_token = "fresh"
    sub = Submission.model_validate(user_submission)

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

        results = await asyncio.gather(
            *(s.create_invoice_draft(sub, request) for _ in range(3))
        )

    assert len(httpx_mock.get_requests(url=cfg.PAYPAL_OAUTH_URL)) == 1
    assert all(result is not None for result in results)
    assert s.PAYPAL_TOKEN.access_token == new_token


class FakeDBPool:
    """Captures the query/args instead of touching a real database."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, query: str, *args) -> None:
        self.calls.append((query, args))


class FakeGmail:
    """Captures the email instead of sending over SMTP."""

    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, email) -> None:
        self.sent.append(email)


class PaypalInvoice(BaseModel):
    detail: Detail


@pytest.mark.e2e
async def test_submission_draft_is_created_then_email_is_sent(
    sandbox_cfg: Settings, user_submission: dict
):
    s.PAYPAL_TOKEN = s.PaypalToken(access_token="", expires_in=0, expires_at=None)
    s.TOKEN_LOCK = asyncio.Lock()
    fake_db = FakeDBPool()
    fake_gmail = FakeGmail()
    external_state = False

    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps(user_submission).encode(),
            "more_body": False,
        }

    async with httpx.AsyncClient() as client:
        req = Request(
            {
                "type": "http",
                "state": {
                    "cfg": sandbox_cfg,
                    "httpx": client,
                    "db": fake_db,
                    "gmail": fake_gmail,
                },
            },
            receive=receive,
        )

        resp = await s.process_submission(req)
        assert resp.status_code == 200
        external_state = True
        assert resp.background is not None, (
            "submission was not persisted to the database"
        )
        await resp.background()

        assert len(fake_gmail.sent) == 1
        sent_email: BookingLead = fake_gmail.sent[0]
        assert sent_email.invoice_url is not None
        assert len(fake_db.calls) == 1

        invoice_id = str(sent_email.invoice_url).rsplit("/", 2)[-2]
        try:
            # fetch invoice using the same token and ensure the reference id is present
            resp = await client.get(
                f"{sandbox_cfg.PAYPAL_INVOICE_DRAFT_URL}/{invoice_id}",
                headers={"Authorization": f"Bearer {s.PAYPAL_TOKEN.access_token}"},
            )
            assert not resp.is_error, "couldn't fetch the invoice linked in the s email"
            raw = resp.json()
            body = PaypalInvoice.model_validate(raw)
            assert body.detail.reference == sent_email.id, (
                f"invoice draft generated doesn't contain the submission reference id:\n{sent_email.id}\n{raw['detail']}"
            )
        finally:
            if external_state:
                resp = await client.delete(
                    f"{sandbox_cfg.PAYPAL_INVOICE_DRAFT_URL}/{invoice_id}",
                    headers={"Authorization": f"Bearer {s.PAYPAL_TOKEN.access_token}"},
                )
                assert not resp.is_error, (
                    f"draft delete request had a failing status code:\n{resp.read()}"
                )
