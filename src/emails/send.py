# from email.message import EmailMessage
import asyncio
from smtplib import SMTP

from jinja2 import Environment, PackageLoader, select_autoescape
from pydantic import EmailStr

from ..cfg import Settings

_env = Environment(
    loader=PackageLoader("emails", "templates"),
    autoescape=select_autoescape(["html"]),
)


# Renders the named template (relative to emails/templates/) with the given field values
def render_email(template_name: str, values: dict) -> str: ...


# Composes the message and sends it over a fresh, single-use SMTP connection
async def send_email(
    cfg: Settings, recipient: EmailStr, template_name: str, values: dict
) -> None:
    def send():
        with SMTP("smpt.gmail.com", 587) as client:
            client.starttls()
            client.login(cfg.SMPT_LOGIN, cfg.SMPT_PASSWORD)

    await asyncio.to_thread(send)
