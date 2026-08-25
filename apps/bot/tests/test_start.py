import asyncio
import logging
from types import SimpleNamespace

import httpx

from app.start import API_UNAVAILABLE_MESSAGE, handle_start


class FakeMessage:
    def __init__(self) -> None:
        self.from_user = SimpleNamespace(id=123, first_name="Анна")
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


class FailingApiClient:
    def __init__(self, error: httpx.HTTPError) -> None:
        self.error = error

    async def create_or_get_user(self, telegram_user: object) -> None:
        raise self.error


def test_start_handles_api_timeout(caplog) -> None:
    message = FakeMessage()

    with caplog.at_level(logging.ERROR):
        asyncio.run(handle_start(message, FailingApiClient(httpx.ReadTimeout("timed out"))))

    assert message.answers == [API_UNAVAILABLE_MESSAGE]
    assert "Could not create or get Telegram user through API" in caplog.text


def test_start_handles_api_http_error(caplog) -> None:
    message = FakeMessage()
    request = httpx.Request("POST", "http://api/users/telegram")
    response = httpx.Response(503, request=request)
    error = httpx.HTTPStatusError("service unavailable", request=request, response=response)

    with caplog.at_level(logging.ERROR):
        asyncio.run(handle_start(message, FailingApiClient(error)))

    assert message.answers == [API_UNAVAILABLE_MESSAGE]
    assert "Could not create or get Telegram user through API" in caplog.text
