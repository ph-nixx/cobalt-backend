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
