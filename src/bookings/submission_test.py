import asyncio
from datetime import UTC, datetime

import httpx
from pytest_httpx import HTTPXMock
from starlette.requests import Request

from cfg import Settings

from . import submission


def _cfg() -> Settings:
    return Settings(
        PAYPAL_WEBHOOK_ID="DEFAULT",
        PAYPAL_CREDS="test-creds",
        PG_URL="",
        SMTP_USER="",
        SMTP_PASSWORD="",
    )


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
