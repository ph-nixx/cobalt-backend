# from email.message import EmailMessage
import asyncio
import logging
from collections.abc import Callable
from queue import Queue
from smtplib import SMTP, SMTPServerDisconnected, _SendErrs
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


type EmailRequest = Callable[[], _SendErrs]
type RequestHandle = asyncio.Future[_SendErrs]
type MainEventLoop = asyncio.AbstractEventLoop
type Work = tuple[EmailRequest, Email, RequestHandle, MainEventLoop]


class Gmail:
    _HOST = "smtp.gmail.com"
    _PORT = 587

    def __init__(self, smtp_user: str, smtp_password: str, interval: int = 300) -> None:
        """
        * interval: seconds between each poll

        NOTE: instantiation must occur inside an event loop

        Function may raise any `smtplib` exceptions.
        """

        self._smtp = SMTP(self._HOST, self._PORT)
        self._smtp.starttls()
        self._smtp.login(smtp_user, smtp_password)
        self._user = smtp_user
        self._password = smtp_password
        self._work: Queue[Work | None] = Queue()
        self._lock = Lock()
        self._stop = Event()
        self._env = Environment(
            loader=PackageLoader("emails", "templates"),
            autoescape=select_autoescape(["html"]),
        )
        self._poller_thread = Thread(target=lambda: self._poller(interval), daemon=True)
        self._poller_thread.start()
        self._worker_thread = Thread(target=self._worker, daemon=True)
        self._worker_thread.start()

    def __enter__(self) -> "Gmail":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Signals both threads to stop, waits for queued work to drain, and closes the SMTP session cleanly."""
        self._stop.set()
        self._work.put(None)
        self._poller_thread.join()
        self._worker_thread.join()
        with self._lock:
            self._smtp.quit()

    def _poller(self, interval: int) -> None:
        """Keeps the SMTP connection alive until told to stop, reconnecting on disconnect and ignoring any other polling error."""
        while not self._stop.wait(interval):
            try:
                with self._lock:
                    self._smtp.noop()
            except SMTPServerDisconnected:
                with self._lock:
                    self._reconnect()
            except Exception:
                pass

    def _worker(self) -> None:
        """Drains queued requests until told to stop, retrying once after a reconnect on disconnect, and logs any other failure instead of dying."""
        while True:
            item = self._work.get()
            if item is None:
                break
            req, email, fut, loop = item
            error: Exception | None = None
            try:
                with self._lock:
                    result = req()
            except SMTPServerDisconnected:
                try:
                    with self._lock:
                        self._reconnect()
                        result = req()
                except Exception as retry_error:
                    error = retry_error
            except Exception as e:
                error = e

            if error is not None:
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
                continue

            loop.call_soon_threadsafe(fut.set_result, result)

    def _reconnect(self):
        self._smtp.close()
        self._smtp = SMTP(self._HOST, self._PORT)
        self._smtp.starttls()
        self._smtp.login(self._user, self._password)

    async def send(self, email: Email):
        """Hands request to the work queue and yields execution."""
        fut: RequestHandle = asyncio.Future()
        loop = asyncio.get_running_loop()
        work = lambda: self._smtp.sendmail(email.sender, email.recipient, "")
        self._work.put((work, email, fut, loop))
        result = await fut
