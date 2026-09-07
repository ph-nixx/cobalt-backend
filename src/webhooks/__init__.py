import logging

from starlette.routing import Route

logger = logging.getLogger(__name__)

from .paypal import record_payed_invoice

routes = [Route("/api/hooks/paypal", record_payed_invoice, methods=["POST"])]
