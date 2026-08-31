from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from asyncpg import Pool
from pydantic import BaseModel, EmailStr, Field, ValidationError
from pydantic_extra_types.phone_numbers import PhoneNumber
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..emails import BookingLead, EmailNotSent


class Submission(BaseModel):
    # this field shouldn't be sent by the client, it's server side meta data
    id: UUID = Field(default_factory=uuid4, init=False)

    # the only TS implementation makes allows the client to send the data and time seperately
    # this implementation forces the client to join them before sending
    name: str
    date_booked: datetime
    email: EmailStr
    phone: PhoneNumber

    # the backend controls what form submission values are valid
    vehicle: Literal["suv", "sprinter"]
    service: Literal["airport", "corporate", "day-trip", "masters", "wedding", "other"]
    notes: str
    first_click: datetime | None = None
    gclid: str | None = None
    gbraid: str | None = None
    wbraid: str | None = None

    def as_row(self) -> tuple:
        return (
            self.id,
            self.email,
            self.phone,
            self.gclid,
            self.gbraid,
            self.wbraid,
            self.first_click,
        )


async def process_submission(request: Request) -> Response:
    try:
        submission = Submission.model_validate_json(await request.body())
    except ValidationError as e:
        return JSONResponse(
            {"invalid_fields": [err["loc"][0] for err in e.errors()]}, status_code=400
        )

    try:
        await request.state.gmail.send(
            BookingLead(
                sender=request.state.cfg.SMTP_USER,
                recipient=submission.email,
                name=submission.name,
                service=submission.service,
                phone=submission.phone,
                vehicle=submission.vehicle,
            )
        )
    except EmailNotSent:
        return JSONResponse({}, status_code=500)

    return Response(
        status_code=200,
        background=BackgroundTask(persist_submission, submission, request.state.db),
    )


async def persist_submission(submission: Submission, db: Pool):
    await db.execute(
        """"
        INSERT INTO quote_requests (
            id, 
            email, 
            phone, 
            gclid, 
            gbraid, 
            wbraid, 
            first_click
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        submission.as_row(),
    )
    await db.close()
