import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


def validate_email(value: str) -> str:
    # Reject control characters BEFORE trimming; SQLite/PG trim only spaces.
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) > 126 for char in value):
        raise ValueError("Email must use printable ASCII")
    value = value.strip(" ")
    if len(value) > 320 or value.count("@") != 1:
        raise ValueError("Invalid email")
    local, domain = value.split("@")
    if not local or len(local) > 64 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local):
        raise ValueError("Invalid email")
    labels = domain.split(".")
    if len(labels) < 2 or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels):
        raise ValueError("Invalid email")
    return value


class AuthCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    email: str
    password: SecretStr

    @field_validator("email", mode="before")
    @classmethod
    def email_policy(cls, value):
        return validate_email(value)

    @field_validator("password")
    @classmethod
    def password_policy(cls, value: SecretStr):
        password = value.get_secret_value()
        if any(0xD800 <= ord(char) <= 0xDFFF for char in password):
            raise ValueError("Password must use valid Unicode")
        if not 15 <= len(password) <= 128:
            raise ValueError("Password must contain 15 to 128 characters")
        return value


class RegisterIn(AuthCredentials):
    display_name: str | None = Field(default=None, max_length=255)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value):
        return value.strip() or None if value is not None else None


class CurrentUserOut(BaseModel):
    email: str | None
    email_verified: bool
    telegram_linked: bool
    display_name: str | None
    profile_exists: bool
    created_at: datetime


class LoginOut(BaseModel):
    session_token: str = Field(repr=False)
    expires_at: datetime
    me: CurrentUserOut
