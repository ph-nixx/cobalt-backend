import contextlib
from collections.abc import AsyncIterator
from typing import TypedDict

import asyncpg
from pydantic import Field
from pydantic_settings import BaseSettings
from starlette.applications import Starlette

from emails import Gmail


class Settings(BaseSettings):
    # WEBHOOK_ID is the sandbox default id
    PAYPAL_WEBHOOK_ID: str
    PG_URL: str
    PG_MIN_POOL: int = Field(gt=0, default=2)
    PG_MAX_POOL: int = Field(gt=0, default=10)
    SMPT_USER: str
    SMPT_PASSWORD: str


class State(TypedDict):
    cfg: Settings
    db: asyncpg.Pool
    gmail: Gmail


@contextlib.asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[State]:
    cfg = Settings()
    async with asyncpg.create_pool(
        cfg.PG_URL, min_size=cfg.PG_MIN_POOL, max_size=cfg.PG_MAX_POOL
    ) as db:
        with Gmail(cfg.SMPT_USER, cfg.SMPT_PASSWORD) as gmail:
            yield State(cfg=cfg, db=db, gmail=gmail)
