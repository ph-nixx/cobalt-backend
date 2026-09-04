# <YYYY-MM-DD>


(Describe the important or surprising ideas or results)

```txt
(stdout or raw metrics)
```

(Clarify any insights, decision rationale, or next steps)

---

# <2026-08-23>


We don't need to use a ORM.

- The only read operation is: check if this invoice id matches a quote with a google ads identifier

- The only write operation is: record this invoice that has a quote with a google ads identifier

---

# <2026-08-27>

Working out how a coroutine on the main event loop can hand blocking work to a worker
thread and yield until it's done "communication with a callback,".

- Queue item is the blocking request itself "why would you separate the future from the work";
  future is a channel (communication mechanism) via `call_soon_threadsafe`, not part of the work.
- Asked whether a `Task` "a future with work assigned to it" could be submitted to the queue directly.
  No, a `Task` needs an event loop to drive its coroutine, if the worker thread has no loop, 
  only synchronus function calls will execute.
- Considered giving the worker thread its own event loop + `asyncio.Queue` so a
  poll coroutine could interleave with job draining.
- Decided against it "using a second thread is a much simpler approach... all it
  requires is that we lock the SMTP object whenever we access it."
- Refined the locking: any thread will hold the lock for the full duration of use, not just the swap,
  to avoid a stale connection race between the poller and worker threads.

`src/emails/send.py` matches this design conceptually (shared lock on any SMTP usage, worker and poller threads),
but has separate bugs: ctor uses `self._user`/`self._password` before they're assigned, 
`_worker`/`_poller` are never actually started as threads, and `send()` awaits but never returns the result.

---

# <2026-08-28>

Reviewed `src/emails/gmail.py`'s threading/error-handling and worked out a full
failure-handling contract for it, then added a context-manager shutdown path.

- An unhandled exception in `_worker`/`_poller` doesn't crash the process it kills that
  daemon thread silently, and since nothing ever called `fut.set_exception`, the caller's
  `await send(...)` hung.
- Considered hard-crashing (`os._exit(1)`) on any unexpected error. Rejected as the default:
  crash-vs-continue doesn't actually prevent silently losing an email, since the queue is
  in-memory and a crash loses everything still queued anyway. What actually prevents silent
  loss is durability + visibility, not the crash decision — those are orthogonal.
- Decided: persist failed sends to a JSONL by the main logger instead of crashing, and avoid a panic as much as possible.
- Locked the contract: `_worker` retries once after a reconnect on `SMTPServerDisconnected`,
  otherwise persists the failure and keeps draining the queue; `_poller` reconnects on
  disconnect, otherwise ignores the error entirely (a poll failure isn't a missed email).
- The future for a failed send must still be failed (`fut.set_exception`), not just logged —
  the caller is an HTTP handler that needs to turn it into an error response.
- Refined further: persistence doesn't belong in this module at all. A sub-logger's
  handler/formatter owns writing JSONL; `_worker` just calls `logger.error(..., extra={...})`
  with the failure fields.
- Added `__enter__`/`__exit__` for thread cleanup. `_poller`/
  `_worker` had no stop signal and no way to wake from their blocking calls. Needed a
  `threading.Event` (`_poller` uses `event.wait(interval)` instead of `time.sleep`) and a
  `None` sentinel pushed through the queue to unblock `_worker.get()`.
- Follow-up review of `gmail.py` found: `_poller`'s
  `_reconnect()` call is still unprotected, and `send()` has no guard
  against being called after (or racing) `__exit__`, so a future can still hang.

---

# <2026-08-29>

Continued hardening `src/emails/gmail.py`'s shutdown and error-handling; several
approaches tried and discarded before landing on the current design.

- Fixed `_poller`'s unprotected reconnect call (now the thread can't terminate on a reconnect). 
  Decided `_poller` should log reconnect failures too, not just ignore
  them for long idle periods could hide a SMTP connection problems.
  Added exception name dedup so repeated identical failures don't spam the log.
- Tried an `Event`-based open/closed flag for `__exit__` to wait on. Worse than the
  bug it fixed: deadlocks on first use (Event starts cleared), breaks entirely on
  the failure path (`_open.set()` never runs if `await fut` raises), and risks
  deadlocking the event loop itself if `__exit__` runs on the loop's own thread.
  Abandoned.
- Switched to `Queue.shutdown()` (project requires Python >=3.14, confirmed available). 
  Better fit: rejects new `send()` calls immediately instead of hanging, drains already-queued work, 
  no manual sentinel needed.
- Confirmed via docs: `await future` raises whatever was passed to
  `set_exception()` this is why `send()` needs no manual exception check.
- A generic `_try_reconnect` helper was introduced, but narrowing its callers'
  `except` to `SMTPException` reopened the silent-thread-death bug raw
  `OSError`s (DNS failure, connection refused, timeout) aren't `SMTPException`.
  Also found `_poller`'s retry-after-reconnect used a stale, pre-bound method
  (retried against the old closed connection, not the new one).
- Confirmed via docs: `SMTPException` is a subclass of `OSError`, not the reverse
  — so `OSError` alone would cover all smtplib/network failures, but not bugs in
  our own code, which is why `except Exception` is the safer choice.
- Explored hard-crashing the whole process (`os._exit()`) on unexpected errors,
  then decided against it in favor of isolating failures to just the `Gmail` object.
- The self-isolating "panic" approach (`Queue.shutdown(immediate=True)` from
  inside a failing thread) had two problems: asymmetric shutdown detection
  (the other thread could take minutes to notice), any email
  still queued at panic time got abandoned with its future never resolved.
- Reverted the panic/self-shutdown approach entirely; kept naive "log and
  continue" generic exception handling in both threads.
- That reversion incidentally fixed a long-standing bug: the poller/worker
  reconnect race, since `_try_reconnect`'s full detect-reconnect-retry sequence
  now runs under one continuously-held lock.
- Final fixes confirmed correct: `__exit__`'s `smtp.quit()` guarded against
  masking the real exception, a socket-level timeout (10s) added to bound
  previously-unbounded blocking calls, and explicit exception chaining
  (`raise EmailNotSent from e`) added in `send()`.

---

# <2026-08-30>

Designed a mocking strategy for `Gmail`, used it to write three unit test, 
and a queue-drain throughput benchmark.

- Compared mocking approaches for `Gmail`: subclassing to override `_smtp`, 
  patching `smtplib.SMTP` at the point of use, or constructor
  injection via a `default_factory` param. Chose injection "works like a normal function", no patching indirection.
- First factory draft took `self: Gmail` as an argument despite only reading
  class constants (`_HOST`/`_PORT`/`_TIMEOUT`). Simplified to
  `Callable[[], SMTP] | None = None`; confirmed `_try_reconnect` uses the
  injected factory on reconnect too, not just `__enter__`.
- Wrote `FakeSMTP` (records calls, scriptable to raise once per method) and
  `FakeSMTPFactory` (hands out a scripted sequence, counts calls) as the test doubles.
- `test_bad_request_does_not_effect_preceding` and `test_nonlisted_exception_does_not_panic` 
  run against a real `Gmail` instance with `FakeSMTP` injected; `test_no_redundant_reconnections`
  instead unit-tests `_try_reconnect` directly, bypassing the poller/worker threads entirely for determinism.
- Added `test_queue_drain_throughput`: report-only (prints elapsed time +
  emails/sec, no assert threshold) measured ~9,400 emails/sec draining 500 queued sends with `FakeSMTP`. 
  Rejected a hard-threshold assert (arbitrary, flaky on slow runners) and adding
  `pytest-benchmark` (new dependency for one test).

---

# <2026-08-31>

Hardening of the `gmail.py` `_worker` and `_poller` thread cooperation.

- Replaced `test_no_redundant_reconnections` with two tests that actually run
  `_poller`/`_worker` as real threads: `test_worker_reconnect_is_reused_by_poller`
  and `test_poller_reconnect_is_reused_by_worker`. Scripted `FakeSMTP` to fail
  once on a given method name rather than by call-count or timing, so whichever
  thread gets there first triggers the reconnect.
- Explained the mechanism: single-shot-by-name failures decouple the test from
  *which* thread reconnects; polling with a bounded timeout (`_wait_until`)
  checks the real condition on an interval instead of guessing a fixed
  `sleep()` duration (this maked the outcome deterministic).
- Asked whether the tests actually honor real non-deterministic thread timing.
  Confirmed: yes, `Thread`/`Lock`/`Queue`/`Event` all behave as they would naturally.
  However, the test thread read `gmail._smtp` and `factory.call_count` without holding 
  `gmail._lock`, leaving a race window between `factory.call_count` incrementing and
  `self._smtp` being reassigned inside `_try_reconnect`.
- Considered injecting a lock. Concluded: no injection needed `gmail._lock`
  already exists and is reachable from the test (no real private attributes in
  Python); acquiring it directly around the test's ensures mutual exclusivity for all threads.
  We used the existing `_lock` directly rather than injection.
  `_wait_until` now takes `gmail` and checks its predicate under
  `with gmail._lock:`; every direct read of `gmail._smtp`/`factory.call_count`
  while the threads are still alive is exclusive. No production code was changed.
- Merged the two reconnect tests into one,
  `test_reconnect_is_reused_amongst_threads` chose two sequential phases
  with separate `Gmail`/`FakeSMTP`/`factory` per phase over a single
  continuous stale-twice scenario, keeping each phase's setup and assertions
  independent.

Designed the Jinja2-templated email type system (`_Email` base + per-template
pydantic subclasses) end to end, then reviewed the first pass at wiring a
PayPal invoice into the booking-submission flow.

- Design vision: a base `_Email` type handles all the common Jinja2 details
  (collecting and rendering the template); each concrete email type is a
  pydantic subclass of `_Email` bound to its own template; `Gmail.send(email:
  _Email)` stays a simple, flexible interface — the caller picks a public
  email type, supplies only its required fields, and everything else
  (template selection, rendering, sending) is abstracted away.
- The original `_send`/`render` passed raw rendered HTML straight to smtplib
  with no MIME headers — not a valid email message (no
  Subject/From/To/Content-Type).
- `sender`/`recipient` were leaking into the Jinja render context via
  `model_dump()` — fixed by marking them `Field(exclude=True)`.
- `_Email` needed to be immutable (`frozen=True`) since instances cross a
  thread boundary via the work queue before being rendered.
- Jinja2 has no concept of an `email.message.Message` — it only ever produces
  strings; MIME construction is entirely the job of stdlib
  `email.message.EmailMessage`.
- Jinja2 does support extracting a subject and body independently from one
  template via named `{% block %}`s and `Template.blocks`, as an alternative
  to a `subject` field on the model.
- `EmailMessage.set_content(html, subtype="html")` gives a pure-HTML body;
  `add_alternative(html, subtype="html")` after `set_content(text)` gives a
  multipart text+HTML fallback.
- `smtplib.SMTP.send_message(msg)` is preferable to `sendmail(...)` once you
  have an `EmailMessage`, since it reads `From`/`To` straight from the
  headers.
- Rendering and transport should be separate responsibilities: `_Email._render`
  builds the `EmailMessage`, `Gmail.send` is the only place that calls
  `smtp.send_message`.
- An empty/unset `subject` on `BookingLead` was not a bug — it was the still-open
  design question of who owns subject text (model field vs. per-subclass
  default vs. template block).
- `jinja2.Environment` caches compiled templates automatically (default
  `cache_size=400`, `auto_reload=True`); a separate `bytecode_cache=` option
  exists only for persisting bytecode across process restarts.
- `arbitrary_types_allowed=True` on `_Email.model_config` was dead
  configuration — confirmed via source that `PhoneNumber` and `EmailStr` are
  both pydantic-native and never needed it.
- The `gmail_test.py` breakage went deeper than a rename: the old
  `Email(template=Template(""))` pattern for content-agnostic test emails no
  longer existed, and `Gmail` had no way to inject a test template source.
- Chose an injectable `env: Environment | None` on `Gmail.__init__` over a
  `MockEmail`-that-overrides-`_render`, to avoid test doubles needing to stay
  in sync with `gmail.py` internals.
- `_Email._render` requires no `Gmail` machinery to test — it's a pure
  `(self, env) -> EmailMessage` function, so rendering correctness and
  `Gmail`'s queue/thread/reconnect logic can and should be tested completely
  independently.
- That independent testability didn't actually depend on the earlier
  render/transport separation — even the earlier fused `_send(self, smtp,
  env)` was just as testable with a fake SMTP; the separation only improved
  cleanliness.
- Resolved `gmail_test.py` with a minimal `_StubEmail` (uses the real,
  injected `env`, no override) for Gmail-plumbing tests, and renamed
  `FakeSMTP.sendmail` → `send_message` to match production.
- `EmailMessage.set_content(...)` appends a trailing `"\n"` to the body —
  confirmed by direct execution, used to assert the exact raw HTML body in
  `email_models_test.py`.
- `pydantic_extra_types.PhoneNumber` requires the `phonenumbers` package at
  import time; it wasn't declared as a dependency, so it was added via `uv add
  phonenumbers`.
- The `paypal_test.py` failures seen from a full `uv run pytest` were
  unrelated to the email work — a pre-existing `Settings` field-naming issue.
- Splitting `_Email`/`BookingLead` into `email_models.py`, with tests
  correspondingly split (`_StubEmail` in `gmail_test.py`, `MockEmail` in
  `email_models_test.py`), is a clean separation with no duplication.
- `BookingLead.validate(submission)` in `process_submission` always raises a
  `pydantic.ValidationError` — confirmed by direct reproduction — since
  pydantic won't do attribute-based extraction from an unrelated model
  without `from_attributes=True`, and even with that it would still fail on
  the missing `sender`/`recipient` mapping and the `id: UUID` vs `id: str`
  mismatch.
- `create_invoice_draft` has multiple bugs: a stray literal `$` breaking both
  `Authorization` header values; the invoice-draft call should use `Bearer`
  rather than `Basic`; the request body should be sent via `json=` without
  the extraneous `"body"` wrapper; and `timedelta(seconds=body.expires_in *
  1000)` inflates the cached token's lifetime by 1000x.
