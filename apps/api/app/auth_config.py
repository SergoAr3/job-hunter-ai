"""Central session policy and Bot service credential settings."""
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
    bot_service_token: str = field(repr=False)

    def __post_init__(self):
        if self.environment not in {"development", "production", "test"}:
            raise RuntimeError("Invalid APP_ENV")
        if not valid_secret(self.bot_service_token) or (
            self.environment == "production" and production_placeholder(self.bot_service_token)
        ):
            raise RuntimeError("BOT_API_SERVICE_TOKEN must be a server-only random secret of at least 32 printable characters")


@lru_cache
def get_auth_settings() -> AuthSettings:
    return AuthSettings(
        environment=os.getenv("APP_ENV", "production"),
        bot_service_token=os.getenv("BOT_API_SERVICE_TOKEN", ""),
    )
