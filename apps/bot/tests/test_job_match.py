import asyncio
import os
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageReplyMarkup
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update, User

from app.jobs import (
    ACTIVE_MATCH_MESSAGE_ID,
    ACTIVE_MATCH_DETAILS_CLAIM_ID,
    MATCH_DETAILS_PREFIX,
    MATCH_PROFILE_CALLBACK,
    PROCESSING_MESSAGE,
    SAVED_MESSAGE,
    handle_job_url,
    handle_add_job,
    handle_match_callback,
    format_match_details,
    remove_active_match_inline_keyboard,
)
from app.jobs import AddJobStates
import app.jobs as jobs
from app.menu import ADD_JOB_BUTTON, main_menu_action

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:match-test-token")
import app.main as main_module


class FakeChatActionSender:
    @classmethod
    def typing(cls, *, chat_id: int, bot: object) -> "FakeChatActionSender":
        return cls()

    async def __aenter__(self) -> "FakeChatActionSender":
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None


@pytest.fixture(autouse=True)
def fake_chat_action_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "ChatActionSender", FakeChatActionSender)


class FakeBot:
    def __init__(self) -> None:
        self.edits: list[tuple[int, int]] = []

    async def edit_message_reply_markup(self, *, chat_id: int, message_id: int, reply_markup: object) -> None:
        self.edits.append((chat_id, message_id))


class DelayedCleanupBot(FakeBot):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def edit_message_reply_markup(self, *, chat_id: int, message_id: int, reply_markup: object) -> None:
        self.edits.append((chat_id, message_id))
        self.started.set()
        await self.release.wait()


class FakeMessage:
    next_id = 100

    def __init__(self, text: str | None = None, *, bot: object | None = None) -> None:
        self.text = text
        self.message_id = FakeMessage.next_id
        FakeMessage.next_id += 1
        self.from_user = SimpleNamespace(id=123, first_name="Anna", last_name=None, username="anna", language_code="ru")
        self.chat = SimpleNamespace(id=456)
        self.bot = bot or FakeBot()
        self.answers: list[FakeMessage] = []
        self.reply_markup: object | None = None
        self.deleted = False
        self.fail_match_keyboard_cleanup = False

    async def answer(self, text: str, reply_markup: object | None = None) -> "FakeMessage":
        result = FakeMessage(text, bot=self.bot)
        result.reply_markup = reply_markup
        self.answers.append(result)
        return result

    async def delete(self) -> None:
        self.deleted = True

    async def edit_reply_markup(self, *, reply_markup: object | None = None) -> None:
        if self.fail_match_keyboard_cleanup:
            raise TelegramAPIError(EditMessageReplyMarkup(), "cleanup failed")
        self.reply_markup = reply_markup


class FakeCallback:
    def __init__(self, data: str, message: FakeMessage) -> None:
        self.data = data
        self.message = message
        self.from_user = message.from_user
        self.answered = False

    async def answer(self) -> None:
        self.answered = True


class FakeApi:
    def __init__(self, match: object | None = None) -> None:
        self.match = match or {"score": 80, "verdict": "high", "strengths": [{"value": "Python"}], "gaps": [{"value": "Docker"}], "conflicts": []}
        self.match_calls = 0

    async def create_or_get_user(self, user: object) -> int:
        return 7

    async def save_application(self, user_id: int, source_url: str) -> dict[str, object]:
        return {"application_created": True, "application": {"id": 42}}

    async def get_application_match(self, user_id: int, application_id: int) -> dict[str, object]:
        self.match_calls += 1
        if isinstance(self.match, Exception):
            raise self.match
        return self.match


class DelayedMatchApi(FakeApi):
    def __init__(self, match: object | None = None) -> None:
        super().__init__(match)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def get_application_match(self, user_id: int, application_id: int) -> dict[str, object]:
        self.match_calls += 1
        self.started.set()
        await self.release.wait()
        if isinstance(self.match, Exception):
            raise self.match
        return self.match


def state() -> tuple[MemoryStorage, FSMContext]:
    storage = MemoryStorage()
    return storage, FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=1, user_id=123))


def test_new_job_shows_match_and_match_failure_does_not_break_save() -> None:
    async def scenario() -> None:
        storage, current_state = state()
        await current_state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage("https://example.com/job")
        api = FakeApi()
        await handle_job_url(message, current_state, api)
        assert [item.text for item in message.answers] == [PROCESSING_MESSAGE, SAVED_MESSAGE, "🎯 Совпадение: 80%"]
        keyboard = message.answers[-1].reply_markup
        assert getattr(keyboard, "inline_keyboard")[0][0].callback_data == f"{MATCH_DETAILS_PREFIX}42"
        assert await current_state.get_state() is None

        await current_state.set_state(AddJobStates.waiting_for_url)
        failing = FakeApi(httpx.ReadTimeout("timeout"))
        second = FakeMessage("https://example.com/job-2")
        await handle_job_url(second, current_state, failing)
        assert [item.text for item in second.answers] == [PROCESSING_MESSAGE, SAVED_MESSAGE]
        await storage.close()

    asyncio.run(scenario())


def test_profile_required_and_insufficient_data_messages() -> None:
    async def scenario() -> None:
        storage, current_state = state()
        await current_state.set_state(AddJobStates.waiting_for_url)
        request = httpx.Request("GET", "http://api/match")
        response = httpx.Response(409, request=request, json={"detail": {"code": "PROFILE_REQUIRED"}})
        message = FakeMessage("https://example.com/job")
        await handle_job_url(message, current_state, FakeApi(httpx.HTTPStatusError("profile", request=request, response=response)))
        assert message.answers[-1].text == "🎯 Заполни профиль, чтобы оценить совпадение."
        assert getattr(message.answers[-1].reply_markup, "inline_keyboard")[0][0].callback_data == MATCH_PROFILE_CALLBACK

        await current_state.set_state(AddJobStates.waiting_for_url)
        insufficient = FakeMessage("https://example.com/job-2")
        await handle_job_url(insufficient, current_state, FakeApi({"score": None, "verdict": "insufficient_data"}))
        assert insufficient.answers[-1].text == "🎯 Недостаточно данных для надёжной оценки."
        await storage.close()

    asyncio.run(scenario())


def test_details_callback_cleans_keyboard_and_duplicate_is_stale() -> None:
    async def scenario() -> None:
        storage, current_state = state()
        message = FakeMessage()
        message.reply_markup = object()
        await current_state.update_data({ACTIVE_MATCH_MESSAGE_ID: message.message_id})
        api = FakeApi()
        callback = FakeCallback(f"{MATCH_DETAILS_PREFIX}42", message)
        await handle_match_callback(callback, current_state, api)
        assert callback.answered is True
        assert api.match_calls == 1
        assert message.reply_markup is None
        assert len(message.answers) == 1
        assert "Сильные стороны:" in message.answers[-1].text
        assert "Что проверить:" in message.answers[-1].text

        stale = FakeCallback(f"{MATCH_DETAILS_PREFIX}42", message)
        await handle_match_callback(stale, current_state, api)
        assert stale.answered is True
        assert api.match_calls == 1
        assert len(message.answers) == 1
        await storage.close()

    asyncio.run(scenario())


def test_concurrent_details_callbacks_claim_one_request_and_one_message() -> None:
    async def scenario() -> None:
        storage, current_state = state()
        message = FakeMessage()
        await current_state.update_data({ACTIVE_MATCH_MESSAGE_ID: message.message_id})
        api = DelayedMatchApi()
        first = asyncio.create_task(handle_match_callback(FakeCallback(f"{MATCH_DETAILS_PREFIX}42", message), current_state, api))
        await api.started.wait()
        second = asyncio.create_task(handle_match_callback(FakeCallback(f"{MATCH_DETAILS_PREFIX}42", message), current_state, api))
        await asyncio.sleep(0)
        assert api.match_calls == 1
        api.release.set()
        await asyncio.gather(first, second)
        assert len(message.answers) == 1
        assert (await current_state.get_data()).get(ACTIVE_MATCH_MESSAGE_ID) is None
        assert (await current_state.get_data()).get(ACTIVE_MATCH_DETAILS_CLAIM_ID) is None
        await storage.close()

    asyncio.run(scenario())


def test_details_api_failure_restores_active_summary_for_retry() -> None:
    async def scenario() -> None:
        storage, current_state = state()
        message = FakeMessage()
        message.reply_markup = object()
        await current_state.update_data({ACTIVE_MATCH_MESSAGE_ID: message.message_id})
        api = FakeApi(httpx.ReadTimeout("timeout"))
        await handle_match_callback(FakeCallback(f"{MATCH_DETAILS_PREFIX}42", message), current_state, api)
        assert (await current_state.get_data()).get(ACTIVE_MATCH_MESSAGE_ID) == message.message_id
        assert (await current_state.get_data()).get(ACTIVE_MATCH_DETAILS_CLAIM_ID) is None
        assert message.reply_markup is not None

        api.match = {"score": 80, "verdict": "high", "strengths": [], "gaps": [], "conflicts": []}
        await handle_match_callback(FakeCallback(f"{MATCH_DETAILS_PREFIX}42", message), current_state, api)
        assert api.match_calls == 2
        assert len(message.answers) == 1
        await storage.close()

    asyncio.run(scenario())


def test_navigation_during_claim_does_not_restore_or_send_stale_details() -> None:
    async def scenario() -> None:
        storage, current_state = state()
        bot = DelayedCleanupBot()
        message = FakeMessage(bot=bot)
        await current_state.update_data({ACTIVE_MATCH_MESSAGE_ID: message.message_id})
        api = DelayedMatchApi(httpx.ReadTimeout("timeout"))
        task = asyncio.create_task(handle_match_callback(FakeCallback(f"{MATCH_DETAILS_PREFIX}42", message), current_state, api))
        await api.started.wait()
        message.text = ADD_JOB_BUTTON
        navigation = asyncio.create_task(main_menu_action(message, current_state))
        await bot.started.wait()
        api.release.set()
        await task
        bot.release.set()
        await navigation
        assert await current_state.get_data() == {}
        assert await current_state.get_state() == AddJobStates.waiting_for_url.state
        assert len(message.answers) == 1
        assert bot.edits == [(456, message.message_id)]
        await storage.close()

    asyncio.run(scenario())


def test_details_are_sent_when_keyboard_cleanup_fails() -> None:
    async def scenario() -> None:
        storage, current_state = state()
        message = FakeMessage()
        message.fail_match_keyboard_cleanup = True
        await current_state.update_data({ACTIVE_MATCH_MESSAGE_ID: message.message_id})
        await handle_match_callback(FakeCallback(f"{MATCH_DETAILS_PREFIX}42", message), current_state, FakeApi())
        assert len(message.answers) == 1
        assert (await current_state.get_data()).get(ACTIVE_MATCH_MESSAGE_ID) is None
        assert (await current_state.get_data()).get(ACTIVE_MATCH_DETAILS_CLAIM_ID) is None
        await storage.close()

    asyncio.run(scenario())


def test_active_match_keyboard_cleanup_is_best_effort() -> None:
    async def scenario() -> None:
        storage, current_state = state()
        bot = FakeBot()
        message = FakeMessage(bot=bot)
        await current_state.update_data({ACTIVE_MATCH_MESSAGE_ID: 77})
        await remove_active_match_inline_keyboard(message, current_state)
        assert bot.edits == [(456, 77)]
        await storage.close()

    asyncio.run(scenario())


def test_add_job_cleans_active_match_keyboard_before_starting_flow() -> None:
    async def scenario() -> None:
        storage, current_state = state()
        bot = FakeBot()
        message = FakeMessage(bot=bot)
        await current_state.update_data({ACTIVE_MATCH_MESSAGE_ID: 77})

        await handle_add_job(message, current_state)

        assert bot.edits == [(456, 77)]
        assert await current_state.get_state() == AddJobStates.waiting_for_url.state
        assert await current_state.get_data() == {}
        await storage.close()

    asyncio.run(scenario())


def test_partial_role_is_not_rendered_as_full_match() -> None:
    rendered = format_match_details(
        {"strengths": [], "gaps": [{"code": "role_partial", "value": "Software Data Engineer"}], "conflicts": []}
    )
    assert "Роль совпадает частично: Software Data Engineer" in rendered
    assert "Подходящая роль" not in rendered


def test_workplace_match_explanation_is_neutral_for_any_preference() -> None:
    rendered = format_match_details(
        {
            "strengths": [{"component": "workplace", "code": "workplace_matched", "value": "onsite"}],
            "gaps": [],
            "conflicts": [],
        }
    )
    assert "Формат работы подходит" in rendered
    assert "onsite" not in rendered


def test_workplace_match_hidden_by_strength_limit_is_shown_once_in_other_criteria() -> None:
    strengths = [
        {"component": "role", "code": "role_matched", "value": "Python-разработчик"},
        *[
            {"component": "required_skills", "code": "required_skills_matched", "value": skill}
            for skill in ("Python", "PostgreSQL", "Docker", "Linux", "Git")
        ],
        {"component": "workplace", "code": "workplace_matched", "value": "onsite"},
    ]
    rendered = format_match_details(
        {"strengths": strengths, "gaps": [], "conflicts": [], "components": {"workplace": {"status": "matched"}}}
    )

    assert "Формат работы подходит" not in rendered
    assert rendered.count("🏠 Формат работы: соответствует") == 1


def test_workplace_mismatch_and_unknown_are_rendered_once() -> None:
    mismatch = format_match_details(
        {
            "strengths": [],
            "gaps": [{"component": "workplace", "code": "workplace_missing", "value": "remote"}],
            "conflicts": [],
            "components": {"workplace": {"status": "mismatch"}},
        }
    )
    unknown = format_match_details(
        {"strengths": [], "gaps": [], "conflicts": [], "components": {"workplace": {"status": "unknown"}}}
    )

    assert mismatch.count("Формат работы для проверки: remote") == 1
    assert "🏠 Формат работы:" not in mismatch
    assert unknown.count("🏠 Формат работы: нет данных") == 1


def test_match_details_render_other_component_statuses_without_guessing() -> None:
    rendered = format_match_details(
        {
            "strengths": [{"component": "seniority", "code": "seniority_matched", "value": "senior"}],
            "gaps": [{"component": "languages", "code": "languages_missing", "value": "English B2"}],
            "conflicts": [{"component": "salary", "code": "salary_below_minimum", "value": None}],
            "components": {
                "seniority": {"status": "matched"},
                "languages": {"status": "partial"},
                "workplace": {"status": "mismatch"},
                "location": {"status": "unknown"},
                "salary": {"status": "unknown"},
            },
        }
    )
    assert "Сильные стороны:" in rendered
    assert "Что проверить:" in rendered
    assert "Конфликты:" in rendered
    assert "Другие критерии:" in rendered
    assert "📈 Опыт:" not in rendered
    assert "🌍 Языки:" not in rendered
    assert "💰 Зарплата:" not in rendered
    assert "🏠 Формат работы: есть расхождение" in rendered
    assert "📍 Локация: нет данных" in rendered


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("matched", "🌍 Языки: соответствует"),
        ("partial", "🌍 Языки: частичное совпадение"),
        ("mismatch", "🌍 Языки: есть расхождение"),
        ("unknown", "🌍 Языки: нет данных"),
    ],
)
def test_match_details_render_component_statuses(status: str, expected: str) -> None:
    rendered = format_match_details({"strengths": [], "gaps": [], "conflicts": [], "components": {"languages": {"status": status}}})
    assert expected in rendered
    assert "unknown" not in rendered


def test_dispatcher_routes_match_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        await main_module.dp.fsm.events_isolation.close()
        bot = Bot("123456:match-test-token")
        called: list[str] = []

        async def fake_handler(callback: object, state: object, api_client: object) -> None:
            called.append("match")

        monkeypatch.setattr(main_module, "handle_match_callback", fake_handler)
        telegram_user = User(id=123, is_bot=False, first_name="Anna")
        message = Message(
            message_id=1,
            date=datetime.now(),
            chat=Chat(id=456, type="private"),
            from_user=telegram_user,
            text="match",
        )
        callback = CallbackQuery(
            id="callback-id",
            from_user=telegram_user,
            chat_instance="chat-instance",
            data=f"{MATCH_DETAILS_PREFIX}42",
            message=message,
        )
        await main_module.dp.feed_update(bot, Update(update_id=999, callback_query=callback))
        assert called == ["match"]
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_routes_add_job_command(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        await main_module.dp.fsm.events_isolation.close()
        bot = Bot("123456:match-test-token")
        called: list[str] = []

        async def fake_handler(message: object, state: object) -> None:
            called.append("add_job")

        monkeypatch.setattr(main_module, "handle_add_job", fake_handler)
        telegram_user = User(id=124, is_bot=False, first_name="Anna")
        message = Message(
            message_id=2,
            date=datetime.now(),
            chat=Chat(id=457, type="private"),
            from_user=telegram_user,
            text="/add_job",
            entities=[MessageEntity(type="bot_command", offset=0, length=8)],
        )
        await main_module.dp.feed_update(bot, Update(update_id=1000, message=message))
        assert called == ["add_job"]
        await bot.session.close()

    asyncio.run(scenario())
