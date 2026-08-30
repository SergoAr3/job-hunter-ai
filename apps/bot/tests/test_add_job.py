import asyncio
from types import SimpleNamespace

import httpx
import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.jobs import (
    ALREADY_SAVED_MESSAGE,
    API_UNAVAILABLE_MESSAGE,
    CANCELLED_MESSAGE,
    INVALID_URL_MESSAGE,
    NO_ACTIVE_FLOW_MESSAGE,
    PROCESSING_MESSAGE,
    UNSAFE_URL_MESSAGE,
    REQUEST_URL_MESSAGE,
    SAVED_MESSAGE,
    AddJobStates,
    handle_add_job,
    handle_cancel,
    handle_job_url,
    format_job_card,
)
import app.jobs as jobs


class FakeSentMessage:
    def __init__(self, text: str, *, delete_fails: bool = False) -> None:
        self.text = text
        self.deleted = False
        self.delete_fails = delete_fails

    async def delete(self) -> None:
        self.deleted = True
        if self.delete_fails:
            raise RuntimeError("delete failed")


class FakeChatActionSender:
    instances: list["FakeChatActionSender"] = []
    fail_on_exit = False

    def __init__(self, *, chat_id: int, bot: object) -> None:
        self.chat_id = chat_id
        self.bot = bot
        self.entered = False
        self.exited = False

    @classmethod
    def typing(cls, *, chat_id: int, bot: object) -> "FakeChatActionSender":
        sender = cls(chat_id=chat_id, bot=bot)
        cls.instances.append(sender)
        return sender

    async def __aenter__(self) -> "FakeChatActionSender":
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        self.exited = True
        if self.fail_on_exit:
            raise RuntimeError("typing stop failed")


@pytest.fixture(autouse=True)
def fake_chat_action_sender(monkeypatch):
    FakeChatActionSender.instances = []
    FakeChatActionSender.fail_on_exit = False
    monkeypatch.setattr(jobs, "ChatActionSender", FakeChatActionSender)


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
        self.sent_messages: list[FakeSentMessage] = []
        self.chat = SimpleNamespace(id=456)
        self.bot = object()
        self.processing_delete_fails = False

    async def answer(self, text: str) -> FakeSentMessage:
        self.answers.append(text)
        sent_message = FakeSentMessage(text, delete_fails=text == PROCESSING_MESSAGE and self.processing_delete_fails)
        self.sent_messages.append(sent_message)
        return sent_message


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


class UnsafeUrlApiClient(HttpErrorApiClient):
    async def save_application(self, user_id: int, source_url: str) -> dict[str, object]:
        request = httpx.Request("POST", f"http://api/users/{user_id}/applications")
        response = httpx.Response(422, request=request, json={"detail": "Unsafe URL"})
        raise httpx.HTTPStatusError("unsafe URL", request=request, response=response)


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
        assert message.answers == [PROCESSING_MESSAGE, SAVED_MESSAGE]
        assert message.sent_messages[0].deleted is True
        assert len(FakeChatActionSender.instances) == 1
        assert FakeChatActionSender.instances[0].chat_id == 456
        assert FakeChatActionSender.instances[0].entered is True
        assert FakeChatActionSender.instances[0].exited is True
        await storage.close()

    asyncio.run(scenario())


def test_duplicate_job_has_clear_message() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage("https://example.com/jobs/123")

        await handle_job_url(message, state, FakeApiClient(application_created=False))

        assert await state.get_state() is None
        assert message.answers == [PROCESSING_MESSAGE, ALREADY_SAVED_MESSAGE]
        assert message.sent_messages[0].deleted is True
        await storage.close()

    asyncio.run(scenario())


def test_existing_application_shows_only_already_saved_message() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage("https://example.com/jobs/123")
        client = FakeApiClient(application_created=False)
        client.save_application = lambda user_id, source_url: __import__("asyncio").sleep(0, result={"application_created": False, "job": {"parsing_status": "partial", "title": "Engineer"}})
        await handle_job_url(message, state, client)
        assert message.answers == [PROCESSING_MESSAGE, ALREADY_SAVED_MESSAGE]
        assert message.sent_messages[0].deleted is True
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


def test_cancel_without_active_flow_uses_neutral_message() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage()

        await handle_cancel(message, state)

        assert await state.get_state() is None
        assert message.answers == [NO_ACTIVE_FLOW_MESSAGE]
        await storage.close()

    asyncio.run(scenario())


def test_api_error_keeps_state() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage("https://example.com/jobs/123")

        await handle_job_url(message, state, FailingApiClient())

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert message.answers == [PROCESSING_MESSAGE, API_UNAVAILABLE_MESSAGE]
        assert message.sent_messages[0].deleted is True
        await storage.close()

    asyncio.run(scenario())


def test_api_validation_error_keeps_state_and_requests_valid_url() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage("https://example.com/jobs/123")

        await handle_job_url(message, state, HttpErrorApiClient(422))

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert message.answers == [PROCESSING_MESSAGE, INVALID_URL_MESSAGE]
        assert message.sent_messages[0].deleted is True
        await storage.close()

    asyncio.run(scenario())


def test_unsafe_url_keeps_state_and_requests_another_url() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage("http://127.0.0.1/")
        await handle_job_url(message, state, UnsafeUrlApiClient(422))
        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert message.answers == [PROCESSING_MESSAGE, UNSAFE_URL_MESSAGE]
        assert message.sent_messages[0].deleted is True
        await storage.close()
    asyncio.run(scenario())


def test_api_server_error_keeps_state_and_reports_unavailability() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage("https://example.com/jobs/123")

        await handle_job_url(message, state, HttpErrorApiClient(503))

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert message.answers == [PROCESSING_MESSAGE, API_UNAVAILABLE_MESSAGE]
        assert message.sent_messages[0].deleted is True
        await storage.close()

    asyncio.run(scenario())


def test_job_card_keeps_structured_fields_when_text_is_long() -> None:
    card = format_job_card({"title": "Engineer", "company": "Acme", "location": "Yerevan", "salary_text": "$3000", "workplace_type": "remote", "employment_type": "full_time", "description": "x" * 5000, "requirements_text": "y" * 1000})
    assert "Вакансия: Engineer" in card
    assert "Компания: Acme" in card
    assert len(card) <= 3800


def test_job_card_shows_successful_ai_enrichment_without_ai_error_state() -> None:
    card = format_job_card({"title": "Engineer", "ai_enrichment_status": "success", "required_skills": ["Python", "SQL"], "nice_to_have_skills": ["Docker"], "seniority": "senior", "responsibilities": ["Build APIs"]})
    assert "Навыки: Python; SQL" in card
    assert "Будет плюсом: Docker" in card
    assert "Уровень: senior" in card
    assert "Задачи: Build APIs" in card


def test_job_card_ignores_ai_fields_when_enrichment_failed() -> None:
    card = format_job_card({"title": "Engineer", "ai_enrichment_status": "failed", "required_skills": ["Python"], "seniority": "senior"})
    assert card == "Вакансия: Engineer"


def test_job_card_is_not_empty_when_only_ai_fields_are_displayable() -> None:
    card = format_job_card({"ai_enrichment_status": "success", "required_skills": ["Python"], "seniority": "senior"})
    assert card == "Навыки: Python\nУровень: senior"


def test_processing_message_delete_failure_does_not_break_successful_flow() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage("https://example.com/jobs/123")
        message.processing_delete_fails = True

        await handle_job_url(message, state, FakeApiClient())

        assert message.answers == [PROCESSING_MESSAGE, SAVED_MESSAGE]
        assert message.sent_messages[0].deleted is True
        assert await state.get_state() is None
        await storage.close()

    asyncio.run(scenario())


def test_typing_stop_failure_does_not_break_successful_flow() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage("https://example.com/jobs/123")
        FakeChatActionSender.fail_on_exit = True

        await handle_job_url(message, state, FakeApiClient())

        assert message.answers == [PROCESSING_MESSAGE, SAVED_MESSAGE]
        assert message.sent_messages[0].deleted is True
        assert await state.get_state() is None
        await storage.close()

    asyncio.run(scenario())
