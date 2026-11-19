Every booking request on Cobalt Transportation's website emails the owner before
the form responds, and sending that email used to the form about 3 seconds to process. 
The form now processes in under 1 second.

Instead of connecting to Gmail for every form submission, the backend now keeps one connection 
open in the background and reuses it.  When an email fails, the form reports the error to the customer
instead of hanging, and an alert email is sent out.

Built with Python and [Starlette](https://starlette.dev/). A work queue passes emails from the async request handlers 
to two background threads that share one long-lived Gmail SMTP connection: 
one sends emails, the other keeps the connection alive.

Opening a connection is the slow part of sending an email. Before a message can
go out, the server has to connect to Gmail, set up encryption, and log in. Each
step waits on a reply from Gmail, and the old backend went through all of them
on every booking request.

Keeping one connection open avoids that cost, but it creates problems of its
own. Gmail drops connections that sit idle, so the connection can go stale
between bookings without anyone noticing. Python's [smtplib](https://docs.python.org/3/library/smtplib.html) also blocks: 
while it waits on Gmail, the server can't do anything else.

Starlette serves every request from a single async event loop, so one blocking
`smtplib` call holds up every request in flight. The SMTP work has to move to a
background thread, and that brings in the usual threading hazards. The send
thread and the keep-alive thread share one connection, so both can find it
stale at the same moment and race to replace it. If a background thread dies
from an unhandled exception, it fails silently, and the request waiting on its
email hangs forever.

The simplest way to make the form fast would have been to stop waiting for the
email: respond to the customer right away and send the email afterward. The
backend already does this when it saves each booking to the database, which
happens after the response goes out. I chose not to treat the email the same
way. A failed database write loses ad-tracking data. A failed email loses the customer: 
the owner never hears about the request, and the customer has no idea something went wrong. 
Waiting for the send costs the customer a moment. In exchange, if something goes wrong 
they find out immediately and can try again or call.

Systems that queue background work usually hand it to a dedicated message
broker such as RabbitMQ or Amazon SQS, which keeps queued messages safe through
a crash. For a site serving about 120 users a month, a broker would mean running
a second service to protect a handful of emails. The work queue I decided to use is
Python's thread safe [Queue](https://docs.python.org/3/library/queue.html).

Inside the server, the question was how async request handlers should pass
blocking SMTP work to a background thread and wait for the result. One option
was to give the background thread its own event loop and an `asyncio.Queue`, so
sending and keep-alive could run as coroutines side by side. That keeps the
design async throughout, but it means coordinating two event loops. Two plain
threads with one lock are simpler, and to me seems simpler to reason about. Each
thread holds the lock for the whole time it uses the connection, reconnects
included, so they can never race to replace it. The request handler waits on an
asyncio future, and the send thread completes that future when it finishes.

The harder question was what a background thread should do when something
unexpected goes wrong. Crashing the whole process (`os._exit`) is a common way
to make failures impossible to miss, and I considered it. But a crash doesn't
prevent lost email: the queue lives in memory, so crashing throws away
everything still in it. Visibility is what prevents silent loss. Every failed
send is logged, triggers an alert email, and fails its future, so the waiting
request returns an error. A middle ground was to let a failing thread shut down
only the email system (`Queue.shutdown(immediate=True)`), but that had problems
of its own. The keep-alive thread could take up to two minutes to notice the
shutdown, and emails already in the queue were abandoned with their requests
still waiting. Both threads now log the error and keep running.

## Trade offs

The clearest cost is the in-memory queue. If the server crashes, any emails
still waiting in the queue are gone. In practice the exposure is small. At about
120 users a month the queue is almost always empty, and an email only waits in
it until the send thread picks it up. Planned restarts, such as deploys, lose
nothing, because shutdown lets the queue drain before the threads stop. And
because each customer waits for their own email, a crash mid-send cuts off
their request too, so it fails instead of reporting success. A broker would
close the remaining gap, but I'd rather accept a rare, visible failure than run
a second service to prevent it.

Waiting for the email also ties every booking's response time to Gmail. When
Gmail is slow, the form is slow. There is a single send thread, so emails go out
one at a time and bookings that arrive together wait in line. A 10-second
timeout on each network call stops an unresponsive Gmail from holding a request
forever, but it doesn't make a slow Gmail fast. At this traffic, two bookings
rarely arrive at the same moment.

The keep-alive doesn't catch every dead connection. When Gmail closes the
connection outright, `noop()` raises an error and the keep-alive thread
reconnects. When Gmail instead replies with a 421 ("service closing"),
`smtplib` returns the code without raising, so the connection looks healthy
until the next real send fails on it. That booking then pays the full reconnect
cost, the same handshake the long-lived connection exists to avoid.

Error alerts are sent through the same Gmail connection they report on. When
Gmail itself is the problem, the alert can't go out, and the only record is the
log file on the server. Customers still see the error, so no request disappears
unnoticed, but nobody is alerted to a Gmail outage until someone reads the logs.

The design is backed by the tests in [`gmail_test.py`](https://github.com/ph-nixx/cobalt-backend/blob/main/src/emails/gmail_test.py). They run the real `Gmail`
class with its real threads, lock, and queue, and replace only the network.
`Gmail` takes a `default_factory` that builds its SMTP connection, so the tests
pass in a `FakeSMTP` that records every call and can be scripted to fail once on a chosen method. No library code is patched.

- **A failed email is reported to the customer who sent it, and affects no one
  else.** `test_bad_request_does_not_effect_preceding` queues two emails, and
  the first send fails. The first caller gets `EmailNotSent` with the original
  error attached, and the second email still goes out.
- **An unexpected error doesn't kill the send thread.**
  `test_nonlisted_exception_does_not_panic` raises a `RuntimeError` in the
  middle of a send. It then checks that the send thread is still alive and
  sends another email through it.
- **The two threads never race to reconnect.**
  `test_reconnect_is_reused_amongst_threads_not_replaced_redundantly` runs with
  the keep-alive thread checking the connection every 10 ms. First the send
  thread finds the connection stale, then the keep-alive thread does. Each time,
  the other thread must reuse the new connection rather than open another, and
  the test checks this by counting the connections the factory builds. The test
  reads shared state under the same lock the threads use, so its own checks
  can't race either.
- **The work queue adds almost nothing to a send.** With network delay removed,
  the send thread drained 500 queued emails at about 9,400 per second, roughly
  0.1 ms each. The wait a customer sees comes from Gmail, not from passing work
  between threads.
