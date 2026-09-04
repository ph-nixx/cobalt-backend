from datetime import datetime
from email.message import EmailMessage

from jinja2 import Environment
from pydantic import UUID4, BaseModel, ConfigDict, EmailStr, Field, HttpUrl, PrivateAttr
from pydantic_extra_types.phone_numbers import PhoneNumber


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
        msg["From"] = self.sender
        msg["To"] = self.recipient
        msg["Subject"] = self.subject
        return msg


class BookingLead(_Email):
    _template_name: str = PrivateAttr(default="booking_lead.html")

    id: UUID4
    datetime: datetime
    name: str
    email: str
    phone: PhoneNumber
    service: str
    vehicle: str
    invoice_url: HttpUrl | None = None
    notes: str | None = None

    # @computed_field
    # @property
    # def invoice_url(self) -> str:
    #     return f"https://cobalttransport.com/api/bookings/{self.id}/invoice"
