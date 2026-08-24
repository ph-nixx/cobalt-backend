from starlette.routing import Route

from .submission import process_submission

routes = [Route("/api/bookings", process_submission, methods=["POST"])]
