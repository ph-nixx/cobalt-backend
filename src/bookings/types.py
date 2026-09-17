from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import AfterValidator
from pydantic_extra_types.phone_numbers import PhoneNumber, PhoneNumberValidator

from . import logger

type E164 = Annotated[
    PhoneNumber, PhoneNumberValidator(default_region="US", number_format="E164")
]

_EASTERN = ZoneInfo("America/New_York")


def _default_to_eastern(value: datetime) -> datetime:
    """Assume US Eastern local time, resolving DST-fold ambiguity to the earlier offset, for a timestamp missing a UTC offset."""
    if value.tzinfo is not None:
        return value

    resolved = value.replace(tzinfo=_EASTERN, fold=0)
    if resolved.utcoffset() != value.replace(tzinfo=_EASTERN, fold=1).utcoffset():
        logger.warning(
            "Ambiguous DST-fold timestamp %s defaulted to fold=0 (%s)",
            value.isoformat(),
            resolved.utcoffset(),
        )
    return resolved


type LocalDatetime = Annotated[datetime, AfterValidator(_default_to_eastern)]
