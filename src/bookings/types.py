from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator
from pydantic_extra_types.phone_numbers import PhoneNumber, PhoneNumberValidator

type E164 = Annotated[
    PhoneNumber, PhoneNumberValidator(default_region="US", number_format="E164")
]


def _strip_to_naive_utc(value: datetime) -> datetime:
    """Convert an aware timestamp to a naive UTC instant, matching the naive `timestamp` column it's stored in."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


type LocalDatetime = Annotated[datetime, AfterValidator(_strip_to_naive_utc)]
