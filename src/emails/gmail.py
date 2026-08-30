# from email.message import EmailMessage
import asyncio
import logging
from collections.abc import Callable
from queue import Queue, ShutDown
from smtplib import SMTP, SMTPServerDisconnected
from threading import Event, Lock, Thread

from jinja2 import Environment, PackageLoader, select_autoescape
from pydantic import BaseModel, EmailStr

logger = logging.getLogger(__name__)


class Template(BaseModel):
    pass


class Email(BaseModel):
    sender: EmailStr
    recipient: EmailStr
    template: Template


type _SendErrs = dict[str, tuple[int, bytes]]
type EmailRequest = Callable[[SMTP], _SendErrs]
type RequestHandle = asyncio.Future[None]
type MainEventLoop = asyncio.AbstractEventLoop
type Work = tuple[EmailRequest, Email, RequestHandle, MainEventLoop]


class EmailNotSent(ShutDown, OSError):
    pass


class Gmail:
    """
    Inteded to be used with a context manager.

    * interval: seconds between each poll
    """

    _HOST = "smtp.gmail.com"
    _PORT = 587
    _TIMEOUT = 10

    def __init__(
        self,
        smtp_user: str,
        smtp_password: str,
        interval: int = 300,
        default_factory: Callable[[], SMTP] | None = None,
    ) -> None:
        self._default_factory = (
            (lambda: SMTP(self._HOST, self._PORT, timeout=self._TIMEOUT))
            if default_factory is None
            else default_factory
        )
        self._user = smtp_user
        self._password = smtp_password
        self._work: Queue[Work] = Queue()
        self._lock = Lock()
        self._stop = Event()
        self._env = Environment(
            loader=PackageLoader("emails", "templates"),
            autoescape=select_autoescape(["html"]),
        )
        self._poller_thread = Thread(target=lambda: self._poller(interval), daemon=True)
        self._worker_thread = Thread(target=self._worker, daemon=True)

    def __enter__(self) -> Gmail:
        self._smtp = self._default_factory()
        self._smtp.starttls()
        self._smtp.login(self._user, self._password)
        self._poller_thread.start()
        self._worker_thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Signals both _work and _poller threads to stop, waits for queued work to drain, and closes the SMTP session."""
        self._stop.set()
        self._work.shutdown()
        self._poller_thread.join()
        self._worker_thread.join()
        with self._lock:
            try:
                self._smtp.quit()
            except Exception:
                pass

    def _poller(self, interval: int) -> None:
        """Keeps the SMTP connection alive until told to stop, reconnecting on disconnect and ignoring any other polling error."""
        errors: set[str] = set()
        while not self._stop.wait(interval):
            try:
                with self._lock:
                    self._try_reconnect(lambda smtp: smtp.noop())
                    errors = set()
                    continue
            except Exception as e:
                error = e

            error_name = str(type(error).__name__)
            if error_name not in errors:
                logger.error(
                    "Poller failed to reconnect: %s",
                    error,
                    extra={
                        "error_type": type(error).__name__,
                        "error_message": str(error),
                    },
                )
                errors.add(error_name)

    def _worker(self) -> None:
        """Drains queued requests until told to stop, retrying once after a reconnect on disconnect, and logs any other failure instead of dying."""
        while True:
            try:
                req, email, fut, loop = self._work.get()
            except ShutDown:
                break

            try:
                with self._lock:
                    # we should extra any relavent info from _SendErrs and log it
                    send_errs = self._try_reconnect(req)
                    loop.call_soon_threadsafe(fut.set_result, None)
                    continue
            except Exception as e:
                error = e

            logger.error(
                "Failed to send email to %s: %s",
                email.recipient,
                error,
                extra={
                    "sender": email.sender,
                    "recipient": email.recipient,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                },
            )
            loop.call_soon_threadsafe(fut.set_exception, error)

    def _try_reconnect[T](self, req: Callable[[SMTP], T]) -> T:
        """Make a request with a SMTP object and try to handle SMTPServerDisconnected once."""
        try:
            return req(self._smtp)
        except SMTPServerDisconnected:
            self._smtp.close()
            self._smtp = self._default_factory()
            self._smtp.starttls()
            self._smtp.login(self._user, self._password)
            return req(self._smtp)

    async def send(self, email: Email):
        """
        Hands request to the work queue and yields execution.

        Possible exceptions:

        * EmailNotSent: Either the `SMTP` object failed to send the email or the work queue has been shutdown
        """
        fut: RequestHandle = asyncio.Future()
        loop = asyncio.get_running_loop()
        req: EmailRequest = lambda smtp: smtp.sendmail(
            email.sender, email.recipient, ""
        )
        try:
            self._work.put((req, email, fut, loop))
            await fut
        except Exception as e:
            raise EmailNotSent from e
