"""Bounded plain-text delivery. Never log credentials, destinations or payloads."""
import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx


@dataclass(frozen=True)
class Outcome:
    kind: str
    code: str | None = None
    retry_after: int | None = None


class ConfigurationFailure(Exception):
    pass


def message_text(destination, application_id, origin):
    local = destination.remind_at.astimezone(ZoneInfo(destination.timezone))
    # Conservative UTF-16 budget; source URLs, notes and profile never enter this message.
    text = ("Напоминание по вакансии\n"
        f"{(destination.title or 'Вакансия без названия')[:160]}\n"
        f"{(destination.company or '')[:120]}\n\n{destination.action[:500]}\n\n"
        f"{local:%d.%m.%Y %H:%M} ({destination.timezone})\n"
        f"{origin}/applications/{application_id}")
    return text.encode("utf-16-le")[:7000].decode("utf-16-le", errors="ignore")


class TelegramSender:
    def __init__(self, token, origin, environment="production", client=None):
        parsed = urlsplit(origin)
        if (not isinstance(token, str) or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,200}", token) or
                not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or
                parsed.path not in ("", "/") or len(origin) > 512 or
                (parsed.scheme != "https" and not (environment in ("development", "test") and
                 parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")))):
            raise ConfigurationFailure("Reminder sender configuration unavailable")
        self._token, self.origin = token, origin.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(10, connect=3), follow_redirects=False)

    async def close(self):
        await self.client.aclose()

    async def send(self, destination, application_id):
        if type(destination.chat_id) is not int or destination.chat_id <= 0:
            return Outcome("failed", "CHAT_UNAVAILABLE")
        try:
            response = await asyncio.wait_for(self.client.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": destination.chat_id, "text": message_text(destination, application_id, self.origin),
                      "link_preview_options": {"is_disabled": True}},
            ), timeout=10)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            return Outcome("retry", "CONNECT_FAILED")
        except (TimeoutError, httpx.HTTPError):
            return Outcome("failed", "DELIVERY_UNCERTAIN")
        if response.status_code == 401:
            return Outcome("configuration", "CONFIGURATION_FAILED")
        if len(response.content) > 65536:
            return Outcome("failed", "DELIVERY_UNCERTAIN")
        try:
            value = response.json()
        except ValueError:
            return Outcome("failed", "DELIVERY_UNCERTAIN")
        if not isinstance(value, dict):
            return Outcome("failed", "DELIVERY_UNCERTAIN")
        result = value.get("result")
        if response.status_code == 200 and value.get("ok") is True and isinstance(result, dict) and type(result.get("message_id")) is int:
            return Outcome("sent")
        if response.status_code == 429 and value.get("ok") is False and value.get("error_code") == 429:
            parameters = value.get("parameters")
            delay = parameters.get("retry_after") if isinstance(parameters, dict) else None
            if type(delay) is not int or not 1 <= delay <= 86400:
                delay = 60
            return Outcome("retry", "RATE_LIMITED", delay)
        if response.status_code in (400, 403, 404) and value.get("ok") is False and value.get("error_code") == response.status_code:
            return Outcome("failed", "CHAT_UNAVAILABLE")
        # Even an HTTP 5xx is not proof of non-delivery. No speculative resend.
        return Outcome("failed", "DELIVERY_UNCERTAIN")
