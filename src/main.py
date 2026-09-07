import uvicorn
from starlette.applications import Starlette

import bookings
import webhooks
from cfg import lifespan

if __name__ == "__main__":
    app = Starlette(
        lifespan=lifespan,
        routes=[*webhooks.routes, *bookings.routes],
    )
    uvicorn.run(app, port=8080)
