import logging

from starlette.routing import Route

logger = logging.getLogger(__name__)

from .submission import process_submission

routes = [Route("/api/bookings", process_submission, methods=["POST"])]

from .submission import Submission as Submission
