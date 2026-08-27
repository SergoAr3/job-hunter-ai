from datetime import datetime
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from app.models import ApplicationStatus

http_url_adapter = TypeAdapter(AnyHttpUrl)


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


class ApplicationCreateIn(BaseModel):
    source_url: str = Field(min_length=1, max_length=2048)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        try:
            http_url_adapter.validate_python(value)
            parsed = urlsplit(value)
        except (ValidationError, ValueError) as error:
            raise ValueError("source_url must be a valid URL") from error
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("source_url must be an absolute http or https URL")
        return value


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    source_url: str
    title: str | None
    company: str | None
    description: str | None
    created_at: datetime


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    job_id: int
    status: ApplicationStatus
    created_at: datetime
    updated_at: datetime


class SavedApplicationOut(BaseModel):
    job: JobOut
    application: ApplicationOut
    job_created: bool
    application_created: bool
