import contextlib
from collections.abc import AsyncIterator
from typing import TypedDict

import asyncpg
from httpx import AsyncClient
from pydantic import Field
from pydantic_settings import BaseSettings
from starlette.applications import Starlette

from emails import Gmail


class Settings(BaseSettings):
    # WEBHOOK_ID is the sandbox default id
    PAYPAL_WEBHOOK_ID: str
    PAYPAL_CREDS: str
    PAYPAL_OAUTH_URL: str = "https://api-m.paypal.com/v1/oauth2/token"
    PAYPAL_INVOICE_DRAFT_URL: str = "https://api-m.paypal.com/v2/invoicing/invoices"
    PG_URL: str
    PG_MIN_POOL: int = Field(gt=0, default=2)
    PG_MAX_POOL: int = Field(gt=0, default=10)
    SMTP_USER: str
    SMTP_PASSWORD: str


class State(TypedDict):
    cfg: Settings
    db: asyncpg.Pool
    gmail: Gmail
    httpx: AsyncClient


@contextlib.asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[State]:
    cfg = Settings()
    async with asyncpg.create_pool(
        cfg.PG_URL, min_size=cfg.PG_MIN_POOL, max_size=cfg.PG_MAX_POOL
    ) as db:
        with Gmail(cfg.SMTP_USER, cfg.SMTP_PASSWORD) as gmail:
            async with AsyncClient() as httpx:
                yield State(cfg=cfg, db=db, gmail=gmail, httpx=httpx)
