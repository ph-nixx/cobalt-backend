import asyncio
import time
from smtplib import SMTPServerDisconnected

import pytest

from .gmail import Email, EmailNotSent, Gmail, Template


class FakeSMTP:
    """Deterministic stand-in for smtplib.SMTP: records calls and can be scripted to raise once per method."""

    def __init__(self, raise_on: dict[str, Exception] | None = None) -> None:
        self.calls: list[str] = []
        self._raise_on = dict(raise_on or {})

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if name in self._raise_on:
            raise self._raise_on.pop(name)

    def starttls(self) -> None:
        self._record("starttls")

    def login(self, user: str, password: str) -> None:
        self._record("login")

    def noop(self) -> tuple[int, bytes]:
        self._record("noop")
        return (250, b"OK")

    def sendmail(self, from_addr: str, to_addrs: str, msg: str) -> dict:
        self._record("sendmail")
        return {}

    def close(self) -> None:
        self._record("close")

    def quit(self) -> None:
        self._record("quit")


class FakeSMTPFactory:
    """default_factory replacement that hands out a scripted sequence of FakeSMTP instances and counts invocations."""

    def __init__(self, instances: list[FakeSMTP]) -> None:
        self._instances = list(instances)
        self.call_count = 0

    def __call__(self) -> FakeSMTP:
        self.call_count += 1
        return self._instances.pop(0)


def _email(recipient: str) -> Email:
    return Email(sender="sender@example.com", recipient=recipient, template=Template())


async def test_bad_request_does_not_effect_preceding():
    """A failed request in the queue is an isolated instance and does not effect the remaining requests in the queue."""
    smtp = FakeSMTP(raise_on={"sendmail": ValueError("boom")})
    factory = FakeSMTPFactory([smtp])

    with Gmail("user", "pass", interval=10_000, default_factory=factory) as gmail:
        results = await asyncio.gather(
            gmail.send(_email("bad@example.com")),
            gmail.send(_email("good@example.com")),
            return_exceptions=True,
        )

    assert isinstance(results[0], EmailNotSent)
    assert isinstance(results[0].__cause__, ValueError)
    assert results[1] is None
    assert smtp.calls.count("sendmail") == 2


async def test_nonlisted_exception_does_not_panic():
    """A non-SMTPServerDisconnected exception from a request is caught, logged, and leaves the worker thread alive."""
    smtp = FakeSMTP(raise_on={"sendmail": RuntimeError("weird failure")})
    factory = FakeSMTPFactory([smtp])

    with Gmail("user", "pass", interval=10_000, default_factory=factory) as gmail:
        with pytest.raises(EmailNotSent):
            await gmail.send(_email("bad@example.com"))

        assert gmail._worker_thread.is_alive()

        await gmail.send(_email("good@example.com"))

    assert smtp.calls.count("sendmail") == 2


def test_no_redundant_reconnections():
    """When the connection is fixed by the poller or worker it should not be replaced until it goes stale."""
    stale_smtp = FakeSMTP(raise_on={"noop": SMTPServerDisconnected()})
    fresh_smtp = FakeSMTP()
    factory = FakeSMTPFactory([fresh_smtp])
    gmail = Gmail("user", "pass", default_factory=factory)
    gmail._smtp = stale_smtp

    gmail._try_reconnect(lambda smtp: smtp.noop())
    assert factory.call_count == 1
    assert gmail._smtp is fresh_smtp

    gmail._try_reconnect(lambda smtp: smtp.noop())
    gmail._try_reconnect(lambda smtp: smtp.noop())

    assert factory.call_count == 1


async def _test_queue_drain_throughput():
    """Reports how fast the worker thread drains a full queue of sends, with network delay eliminated via FakeSMTP."""
    email_count = 500
    smtp = FakeSMTP()
    factory = FakeSMTPFactory([smtp])

    with Gmail("user", "pass", interval=10_000, default_factory=factory) as gmail:
        start = time.perf_counter()
        await asyncio.gather(
            *(gmail.send(_email(f"user{i}@example.com")) for i in range(email_count))
        )
        elapsed = time.perf_counter() - start

    assert smtp.calls.count("sendmail") == email_count
    print(
        f"\ndrained {email_count} emails in {elapsed:.4f}s "
        f"({email_count / elapsed:,.0f} emails/sec)"
    )
