from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # WEBHOOK_ID is the sandbox default id
    PAYPAL_WEBHOOK_ID: str
