"""Central session policy and explicit staged-rollout settings."""
import hmac
import os
import re
from dataclasses import dataclass, field
from datetime import timedelta
from functools import lru_cache


SESSION_ABSOLUTE_LIFETIME = timedelta(days=7)
SESSION_IDLE_LIFETIME = timedelta(hours=24)
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)


# Deliberate defaults/examples, not an entropy estimator.
UNSAFE_SECRET_PARTS = {"replace", "change", "example", "placeholder", "test", "tests", "dev", "development", "dummy", "secret", "password", "changeme"}


def valid_secret(value: str) -> bool:
    return 32 <= len(value) <= 512 and value.isascii() and all(33 <= ord(c) <= 126 for c in value)


def production_placeholder(value: str) -> bool:
    return bool(UNSAFE_SECRET_PARTS.intersection(re.split(r"[^a-z0-9]+", value.lower()))) or value.lower() in {"a" * len(value), "0" * len(value)}


@dataclass(frozen=True)
class AuthSettings:
    environment: str
    rollout_mode: str
    bot_service_token: str = field(repr=False)
    web_dev_api_token: str = field(default="", repr=False)
    web_dev_user_id: int | None = None

    def __post_init__(self):
        if self.environment not in {"development", "production", "test"}:
            raise RuntimeError("Invalid APP_ENV")
        if self.rollout_mode not in {"enforced", "legacy-development"}:
            raise RuntimeError("Invalid AUTH_ROLLOUT_MODE")
        if self.rollout_mode == "legacy-development" and self.environment != "development":
            raise RuntimeError("Legacy identity is only allowed in development")
        if not valid_secret(self.bot_service_token) or (
            self.environment == "production" and production_placeholder(self.bot_service_token)
        ):
            raise RuntimeError("BOT_API_SERVICE_TOKEN must be a server-only random secret of at least 32 printable characters")
        if self.web_dev_api_token or self.web_dev_user_id is not None:
            if self.environment != "development" or self.rollout_mode != "legacy-development":
                raise RuntimeError("Web dev credentials require explicit development legacy mode")
            if not valid_secret(self.web_dev_api_token) or type(self.web_dev_user_id) is not int or self.web_dev_user_id <= 0:
                raise RuntimeError("Configure WEB_DEV_API_TOKEN and positive WEB_DEV_USER_ID together")
            if hmac.compare_digest(self.web_dev_api_token, self.bot_service_token):
                raise RuntimeError("Web and Bot credentials must be distinct")


@lru_cache
def get_auth_settings() -> AuthSettings:
    dev_id = os.getenv("WEB_DEV_USER_ID", "")
    # WEB_DEV_USER_ID alone remains a Web setting; API dev access requires both.
    dev_token = os.getenv("WEB_DEV_API_TOKEN", "")
    if dev_token and not re.fullmatch(r"[1-9][0-9]*", dev_id):
        raise RuntimeError("Invalid WEB_DEV_USER_ID for development credential")
    return AuthSettings(
        environment=os.getenv("APP_ENV", "production"),
        rollout_mode=os.getenv("AUTH_ROLLOUT_MODE", "enforced"),
        bot_service_token=os.getenv("BOT_API_SERVICE_TOKEN", ""),
        web_dev_api_token=dev_token,
        web_dev_user_id=int(dev_id) if dev_token else None,
    )
