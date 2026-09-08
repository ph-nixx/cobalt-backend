from datetime import datetime
from email.message import EmailMessage
from typing import Annotated

from jinja2 import Environment
from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    PlainSerializer,
    PrivateAttr,
)

from bookings.types import E164


def _sanitize_header(value: str) -> str:
    return value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


class _Email(BaseModel):
    model_config = ConfigDict(frozen=True)
    _template_name: str = PrivateAttr()

    sender: EmailStr = Field(exclude=True)
    recipient: EmailStr = Field(exclude=True)
    subject: str = Field(exclude=True, default="")

    def _render(self, env: Environment) -> EmailMessage:
        # template.render_async might be a better option because it means the work queue does not need to
        # block on str parsing, but we have to make the _worker thread use an event loop

        template = env.get_template(self._template_name)
        html = template.render(self.model_dump())
        msg = EmailMessage()
        msg.set_content(html, subtype="html")
        msg["From"] = _sanitize_header(self.sender)
        msg["To"] = _sanitize_header(self.recipient)
        msg["Subject"] = _sanitize_header(self.subject)
        return msg


def english_date(value: datetime) -> str:
    time = value.strftime("%I:%M %p").lstrip("0")
    return f"{value:%B} {value.day}, {value.year} at {time}"


class BookingLead(_Email):
    _template_name: str = PrivateAttr(default="booking_lead.html")

    id: UUID4
    datetime: Annotated[datetime, PlainSerializer(english_date)]
    name: str
    email: str
    phone: E164
    service: str
    vehicle: str
    invoice_url: HttpUrl | None = None
    notes: str | None = None

    # @computed_field
    # @property
    # def invoice_url(self) -> str:
    #     return f"https://cobalttransport.com/api/bookings/{self.id}/invoice"


class LogAlert(_Email):
    _template_name: str = PrivateAttr(default="log_alert.html")

    level: str
    logger_name: str
    message: str
    timestamp: datetime
    exc_info: str | None = None
