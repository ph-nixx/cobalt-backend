from jinja2 import DictLoader, Environment
from pydantic import PrivateAttr

from .email_models import _Email

_TEST_ENV = Environment(loader=DictLoader({"mock.html": "<p>{{ greeting }}</p>"}))


class MockEmail(_Email):
    """_Email subclass with a content field, for verifying _render builds correct headers and renders subclass fields into the template."""

    _template_name: str = PrivateAttr(default="mock.html")

    greeting: str


def test_mock_email_render_produces_expected_message() -> None:
    """Verifies _render builds correct From/To/Subject headers, renders the subclass field into the html body, and excludes envelope fields from it."""
    email = MockEmail(
        sender="sender@example.com",
        recipient="recipient@example.com",
        subject="Test Subject",
        greeting="Hello, World!",
    )

    msg = email._render(_TEST_ENV)

    assert msg["From"] == "sender@example.com"
    assert msg["To"] == "recipient@example.com"
    assert msg["Subject"] == "Test Subject"
    assert msg.get_content_type() == "text/html"
    assert msg.get_content() == "<p>Hello, World!</p>\n"
    assert "sender@example.com" not in msg.get_content()
