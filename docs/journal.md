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
