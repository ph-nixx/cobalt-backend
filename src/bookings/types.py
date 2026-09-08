from typing import Annotated

from pydantic_extra_types.phone_numbers import PhoneNumber, PhoneNumberValidator

type E164 = Annotated[
    PhoneNumber, PhoneNumberValidator(default_region="US", number_format="E164")
]
