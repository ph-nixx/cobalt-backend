# from email.message import EmailMessage
import asyncio
import threading
import time
from collections.abc import Callable
from queue import Queue
from smtplib import SMTP, _SendErrs

from jinja2 import Environment, PackageLoader, select_autoescape
from pydantic import BaseModel, EmailStr


class Template(BaseModel):
    pass


class Email(BaseModel):
    sender: EmailStr
    recipient: EmailStr
    template: Template


type EmailRequest = Callable[[], _SendErrs]
type RequestHandle = asyncio.Future[_SendErrs]
type MainEventLoop = asyncio.AbstractEventLoop
type Work = tuple[EmailRequest, RequestHandle, MainEventLoop]


class Gmail:
    _HOST = "smtp.gmail.com"
    _PORT = 587

    def __init__(self, smtp_user: str, smtp_password: str, interval: int = 300) -> None:
        """
        * interval: seconds between each poll

        NOTE: instantiation must occur inside an event loop

        Function may raise any `smtplib` exceptions.
        """

        self._env = Environment(
            loader=PackageLoader("emails", "templates"),
            autoescape=select_autoescape(["html"]),
        )

        self._smtp = SMTP(self._HOST, self._PORT)
        self._smtp.starttls()
        self._smtp.login(self._user, self._password)
        self._work: Queue[Work] = Queue()
        self._lock = threading.Lock()
        self._user = smtp_user
        self._password = smtp_password

    def _poller(self, interval: int):
        while True:
            time.sleep(interval)
            self._fix_conn()

    def _worker(self):
        """Drains the email requests."""
        while True:
            req, fut, loop = self._work.get()
            try:
                with self._lock:
                    result = req()
            except Exception:
                self._fix_conn()
                with self._lock:
                    result = req()

            loop.call_soon_threadsafe(fut.set_result, result)

    def _fix_conn(self):
        with self._lock:
            try:
                code, _ = self._smtp.noop()
                if code == 250:
                    return
            except Exception:
                # this is temporary and is planned
                # to have exception handling that trys to save the connection
                # and logs each attempt
                pass

            self._smtp = SMTP(self._HOST, self._PORT)
            self._smtp.starttls()
            self._smtp.login(self._user, self._password)

    async def send(self, email: Email):
        """Hands request to the work queue and yields execution."""
        fut: RequestHandle = asyncio.Future()
        loop = asyncio.get_running_loop()
        work = lambda: self._smtp.sendmail(email.sender, email.recipient, "")
        self._work.put((work, fut, loop))
        result = await fut
