import asyncio
import time
from collections.abc import Callable
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
    return Email(
        sender="sender@example.com", recipient=recipient, template=Template("")
    )


async def _wait_until(
    gmail: Gmail,
    predicate: Callable[[], bool],
    timeout: float = 1.0,
    poll: float = 0.005,
) -> bool:
    """Polls a predicate under gmail's lock until it's true or the timeout elapses, so the check is mutually exclusive with its threads."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        with gmail._lock:
            if predicate():
                return True
        await asyncio.sleep(poll)
    with gmail._lock:
        return predicate()


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


async def test_reconnect_is_reused_amongst_threads_not_replaced_redundantly():
    """Once the worker or poller fixes a stale connection, the either thread will not reconnect until the connection becomes stale again."""
    # Phase 1: the worker fixes a stale connection; the poller must reuse it, not reconnect again.
    stale = FakeSMTP(raise_on={"sendmail": SMTPServerDisconnected()})
    fixed = FakeSMTP()
    factory = FakeSMTPFactory([stale, fixed])

    with Gmail("user", "pass", interval=0.01, default_factory=factory) as gmail:
        await gmail.send(
            _email("a@example.com")
        )  # worker hits the stale sendmail and reconnects

        with gmail._lock:
            assert factory.call_count == 2
            assert gmail._smtp is fixed

        # give the poller several cycles against the now-fixed connection
        assert await _wait_until(gmail, lambda: fixed.calls.count("noop") >= 3)

    assert factory.call_count == 2

    # Phase 2: the poller fixes a stale connection; the worker must reuse it, not reconnect again.
    stale = FakeSMTP(raise_on={"noop": SMTPServerDisconnected()})
    fixed = FakeSMTP()
    factory = FakeSMTPFactory([stale, fixed])

    with Gmail("user", "pass", interval=0.01, default_factory=factory) as gmail:
        # wait for the poller to hit the stale noop and reconnect before the worker sends anything
        assert await _wait_until(gmail, lambda: factory.call_count == 2)

        with gmail._lock:
            assert gmail._smtp is fixed

        await gmail.send(_email("a@example.com"))

    assert factory.call_count == 2
    assert fixed.calls.count("sendmail") == 1


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
