from typing import Literal
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator
from app.auth_schemas import AuthCredentials
from app.schemas import TelegramUserIn


class ChallengeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    purpose: Literal["login", "link"]
    binding: SecretStr
    password: SecretStr | None = None

    @field_validator("password")
    @classmethod
    def password_policy(cls, value):
        return AuthCredentials.password_policy(value) if value is not None else None


class BrowserChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    token: SecretStr
    binding: SecretStr
    purpose: Literal["login", "link"]


class BotChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    token: SecretStr


class BotApproval(BotChallenge):
    telegram: TelegramUserIn
