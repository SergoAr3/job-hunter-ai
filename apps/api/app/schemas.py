from pydantic import BaseModel, Field


class TelegramUserIn(BaseModel):
    telegram_id: int = Field(gt=0)
    username: str | None = Field(default=None, max_length=255)
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)
    language_code: str | None = Field(default=None, max_length=16)


class TelegramUserOut(BaseModel):
    id: int
    telegram_id: int
    created: bool
