import asyncio
from types import SimpleNamespace

import httpx
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.jobs import (
    ALREADY_SAVED_MESSAGE,
    API_UNAVAILABLE_MESSAGE,
    CANCELLED_MESSAGE,
    INVALID_URL_MESSAGE,
    REQUEST_URL_MESSAGE,
    SAVED_MESSAGE,
    AddJobStates,
    handle_add_job,
    handle_cancel,
    handle_job_url,
)


class FakeMessage:
    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.from_user = SimpleNamespace(
            id=123,
            first_name="Анна",
            last_name=None,
            username="anna",
            language_code="ru",
        )
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


class FakeApiClient:
    def __init__(self, application_created: bool = True) -> None:
        self.application_created = application_created
        self.user_calls = 0
        self.save_calls: list[tuple[int, str]] = []

    async def create_or_get_user(self, telegram_user: object) -> int:
        self.user_calls += 1
        return 7

    async def save_application(self, user_id: int, source_url: str) -> dict[str, object]:
        self.save_calls.append((user_id, source_url))
        return {"application_created": self.application_created}


class FailingApiClient:
    async def create_or_get_user(self, telegram_user: object) -> int:
        raise httpx.ReadTimeout("timed out")


class HttpErrorApiClient:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    async def create_or_get_user(self, telegram_user: object) -> int:
        return 7

    async def save_application(self, user_id: int, source_url: str) -> dict[str, object]:
        request = httpx.Request("POST", f"http://api/users/{user_id}/applications")
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError("API error", request=request, response=response)


def make_state() -> tuple[MemoryStorage, FSMContext]:
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=1, user_id=123))
    return storage, state


def test_add_job_starts_url_flow() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage()

        await handle_add_job(message, state)

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert message.answers == [REQUEST_URL_MESSAGE]
        await storage.close()

    asyncio.run(scenario())


def test_valid_url_calls_api_and_clears_state() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage("HTTPS://EXAMPLE.COM/jobs/123#details")
        api_client = FakeApiClient()

        await handle_job_url(message, state, api_client)

        assert api_client.user_calls == 1
        assert api_client.save_calls == [(7, "HTTPS://EXAMPLE.COM/jobs/123#details")]
        assert await state.get_state() is None
        assert message.answers == [SAVED_MESSAGE]
        await storage.close()

    asyncio.run(scenario())


def test_duplicate_job_has_clear_message() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage("https://example.com/jobs/123")

        await handle_job_url(message, state, FakeApiClient(application_created=False))

        assert await state.get_state() is None
        assert message.answers == [ALREADY_SAVED_MESSAGE]
        await storage.close()

    asyncio.run(scenario())


def test_invalid_text_keeps_state_and_skips_api() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage("помоги найти работу")
        api_client = FakeApiClient()

        await handle_job_url(message, state, api_client)

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert api_client.user_calls == 0
        assert api_client.save_calls == []
        assert message.answers == [INVALID_URL_MESSAGE]
        await storage.close()

    asyncio.run(scenario())


def test_cancel_clears_state() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage()

        await handle_cancel(message, state)

        assert await state.get_state() is None
        assert message.answers == [CANCELLED_MESSAGE]
        await storage.close()

    asyncio.run(scenario())


def test_api_error_keeps_state() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage("https://example.com/jobs/123")

        await handle_job_url(message, state, FailingApiClient())

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert message.answers == [API_UNAVAILABLE_MESSAGE]
        await storage.close()

    asyncio.run(scenario())


def test_api_validation_error_keeps_state_and_requests_valid_url() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage("https://example.com/jobs/123")

        await handle_job_url(message, state, HttpErrorApiClient(422))

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert message.answers == [INVALID_URL_MESSAGE]
        await storage.close()

    asyncio.run(scenario())


def test_api_server_error_keeps_state_and_reports_unavailability() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage("https://example.com/jobs/123")

        await handle_job_url(message, state, HttpErrorApiClient(503))

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert message.answers == [API_UNAVAILABLE_MESSAGE]
        await storage.close()

    asyncio.run(scenario())
