import asyncio
import contextlib
import datetime as dt
import json
import logging
import logging.handlers
import queue
import traceback
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TypedDict, override

import asyncpg
from httpx import AsyncClient
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.applications import Starlette

from emails import Gmail, LogAlert


class Settings(BaseSettings):
    # WEBHOOK_ID is the sandbox default id

    model_config = SettingsConfigDict(extra="ignore")

    PAYPAL_WEBHOOK_ID: str
    PAYPAL_CREDS: str
    PAYPAL_OAUTH_URL: str = "https://api-m.paypal.com/v1/oauth2/token"
    PAYPAL_INVOICE_DRAFT_URL: str = "https://api-m.paypal.com/v2/invoicing/invoices"
    PG_URL: str
    PG_MIN_POOL: int = Field(gt=0, default=2)
    PG_MAX_POOL: int = Field(gt=0, le=20, default=10)
    SMTP_USER: str
    SMTP_PASSWORD: str
    COBALT_GMAIL: str
    LOG_PATH: Path = Field(default=Path("logs/app.jsonl"))


class State(TypedDict):
    cfg: Settings
    db: asyncpg.Pool
    gmail: Gmail
    httpx: AsyncClient


LOG_RECORD_BUILTIN_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class JSONLFormatter(logging.Formatter):
    def __init__(self, *, fmt_keys: dict[str, str] | None = None) -> None:
        """Store which LogRecord attributes to project into each JSON line, under what output key."""
        super().__init__()
        self.fmt_keys = fmt_keys if fmt_keys is not None else {}

    @override
    def format(self, record: logging.LogRecord) -> str:
        """Render the record, plus any extra= fields, as a single JSON line."""
        always_fields = {
            "message": record.getMessage(),
            "timestamp": dt.datetime.fromtimestamp(
                record.created, tz=dt.UTC
            ).isoformat(),
        }
        if record.exc_info is not None:
            always_fields["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info is not None:
            always_fields["stack_info"] = self.formatStack(record.stack_info)

        message = {
            key: msg_val
            if (msg_val := always_fields.pop(val, None)) is not None
            else getattr(record, val)
            for key, val in self.fmt_keys.items()
        }
        message.update(always_fields)

        for key, val in record.__dict__.items():
            if key not in LOG_RECORD_BUILTIN_ATTRS:
                message[key] = val

        return json.dumps(message, default=str)


class GmailAlertHandler(logging.Handler):
    def __init__(
        self,
        gmail: Gmail,
        cfg: Settings,
        level: int = logging.ERROR,
    ) -> None:
        """Store the Gmail sender, target event loop, and alert recipient for emitting error/critical records as email."""
        super().__init__(level=level)
        self._gmail = gmail
        self._sender = cfg.SMTP_USER
        self._recipient = cfg.COBALT_GMAIL
        self._loop = asyncio.get_running_loop()

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Render the record as a LogAlert email and hand it to Gmail.send on the main event loop without blocking the caller."""
        try:
            alert = LogAlert(
                sender=self._sender,
                recipient=self._recipient,
                subject=f"[{record.levelname}] {record.name}: {record.getMessage()}",
                level=record.levelname,
                logger_name=record.name,
                message=record.getMessage(),
                timestamp=dt.datetime.fromtimestamp(record.created, tz=dt.UTC),
                exc_info="".join(traceback.format_exception(*record.exc_info))
                if record.exc_info
                else None,
            )
            asyncio.run_coroutine_threadsafe(self._gmail.send(alert), self._loop)
        except Exception:
            self.handleError(record)


class Log:
    """Context manager attaching a QueueHandler to the root logger, backed by a QueueListener that fans records out to a JSONL file and Gmail error alerts."""

    def __init__(
        self,
        gmail: Gmail,
        cfg: Settings,
        root_level: int = logging.DEBUG,
    ) -> None:
        """Build the JSONL file handler and Gmail alert handler that the listener will run, without starting anything yet."""
        cfg.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=cfg.LOG_PATH, maxBytes=250_000, backupCount=3
        )
        file_handler.setFormatter(
            JSONLFormatter(
                fmt_keys={
                    "level": "levelname",
                    "message": "message",
                    "timestamp": "timestamp",
                    "logger": "name",
                    "module": "module",
                    "function": "funcName",
                    "line": "lineno",
                    "thread_name": "threadName",
                }
            )
        )
        alert_handler = GmailAlertHandler(gmail, cfg)

        self._root_level = root_level
        self._queue: queue.Queue = queue.Queue(-1)
        self._queue_handler = logging.handlers.QueueHandler(self._queue)
        self._listener = logging.handlers.QueueListener(
            self._queue, file_handler, alert_handler, respect_handler_level=True
        )

    def __enter__(self) -> Log:
        """Capture the running event loop, start the QueueListener thread, and attach its QueueHandler to the root logger."""
        root = logging.getLogger()
        root.setLevel(self._root_level)
        root.addHandler(self._queue_handler)
        self._listener.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Detach the QueueHandler from the root logger and stop the QueueListener thread."""
        self._listener.stop()
        logging.getLogger().removeHandler(self._queue_handler)


@contextlib.asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[State]:
    cfg = Settings()
    async with asyncpg.create_pool(
        cfg.PG_URL, min_size=cfg.PG_MIN_POOL, max_size=cfg.PG_MAX_POOL
    ) as db:
        with Gmail(cfg.SMTP_USER, cfg.SMTP_PASSWORD) as gmail:
            with Log(gmail, cfg):
                async with AsyncClient() as httpx:
                    yield State(cfg=cfg, db=db, gmail=gmail, httpx=httpx)
