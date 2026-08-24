from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse


class Submission(BaseModel):
    pass


async def process_submission(request: Request):
    return JSONResponse({"message": "OK"})
