import asyncio
import logging
from types import SimpleNamespace

import httpx
from aiogram.types import ReplyKeyboardMarkup

from app.menu import ADD_JOB_BUTTON, PROFILE_BUTTON
from app.start import API_UNAVAILABLE_MESSAGE, handle_start


class FakeMessage:
    def __init__(self) -> None:
        self.from_user = SimpleNamespace(id=123, first_name="Анна")
        self.answers: list[tuple[str, object | None]] = []

    async def answer(self, text: str, reply_markup: object | None = None) -> None:
        self.answers.append((text, reply_markup))


class SuccessfulApiClient:
    async def create_or_get_user(self, telegram_user: object) -> int:
        return 7


class FailingApiClient:
    def __init__(self, error: httpx.HTTPError) -> None:
        self.error = error

    async def create_or_get_user(self, telegram_user: object) -> None:
        raise self.error


def test_start_handles_api_timeout(caplog) -> None:
    message = FakeMessage()

    with caplog.at_level(logging.ERROR):
        asyncio.run(handle_start(message, FailingApiClient(httpx.ReadTimeout("timed out"))))

    assert message.answers == [(API_UNAVAILABLE_MESSAGE, None)]
    assert "Could not create or get Telegram user through API" in caplog.text


def test_start_handles_api_http_error(caplog) -> None:
    message = FakeMessage()
    request = httpx.Request("POST", "http://api/users/telegram")
    response = httpx.Response(503, request=request)
    error = httpx.HTTPStatusError("service unavailable", request=request, response=response)

    with caplog.at_level(logging.ERROR):
        asyncio.run(handle_start(message, FailingApiClient(error)))

    assert message.answers == [(API_UNAVAILABLE_MESSAGE, None)]
    assert "Could not create or get Telegram user through API" in caplog.text


def test_start_shows_persistent_main_menu_after_registration() -> None:
    message = FakeMessage()

    asyncio.run(handle_start(message, SuccessfulApiClient()))

    assert len(message.answers) == 1
    greeting, reply_markup = message.answers[0]
    assert greeting == "Привет, Анна! Я помогу с поиском работы."
    assert isinstance(reply_markup, ReplyKeyboardMarkup)
    assert reply_markup.resize_keyboard is True
    assert reply_markup.is_persistent is True
    assert [[button.text for button in row] for row in reply_markup.keyboard] == [
        [ADD_JOB_BUTTON],
        [PROFILE_BUTTON],
    ]
