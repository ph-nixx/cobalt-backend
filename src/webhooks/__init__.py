from starlette.routing import Route

from .paypal import record_payed_invoice

routes = [Route("/api/hooks/paypal", record_payed_invoice, methods=["POST"])]

import logging

logger = logging.getLogger(__name__)
