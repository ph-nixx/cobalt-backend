import uvicorn
from starlette.applications import Starlette

import bookings
import webhooks
from cfg import Settings

if __name__ == "__main__":
    app = Starlette(
        debug=True,
        routes=[*webhooks.routes, *bookings.routes],
    )
    app.state.cfg = Settings()
    uvicorn.run(app, port=8080, log_level="info")
