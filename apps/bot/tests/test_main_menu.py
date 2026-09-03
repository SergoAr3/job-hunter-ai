import asyncio
import os
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.methods import EditMessageReplyMarkup
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, MessageEntity, Update, User

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:menu-test-token")

import app.main as main_module
from app.jobs import NO_ACTIVE_FLOW_MESSAGE, REQUEST_URL_MESSAGE, AddJobStates
from app.menu import (
    ADD_JOB_BUTTON,
    PROFILE_BUTTON,
    PROFILE_LOAD_ERROR_MESSAGE,
    PROFILE_MISSING_MESSAGE,
    PROFILE_SECTION_MESSAGE_ID,
    PROFILE_SECTION_REPLACE_CV_CALLBACK,
    PROFILE_REPLACE_CV_CONTINUE_CALLBACK,
    PROFILE_SETUP_CALLBACK,
    main_menu_action,
    profile_section_edit,
    profile_section_replace_cv,
    profile_section_setup,
)
from app.main import add_job, cancel, dp, profile_setup
from app.profile import (
    ACTIVE_PROFILE_PROMPT_MESSAGE_ID,
    CV_REPLACEMENT_DRAFT_SOURCE,
    PERSISTED_PROFILE_SNAPSHOT,
    PROFILE_DRAFT_SOURCE,
    PROFILE_EDITING_FIELD,
    PROFILE_SECTION_EDIT_CALLBACK,
    PROFILE_CANCELLED_MESSAGE,
    ProfileSetupStates,
    ROLE_PROMPT,
    handle_skills,
    profile_payload,
)


class FakeBot:
    def __init__(self, *, fail_edit: bool = False) -> None:
        self.fail_edit = fail_edit
        self.removed_keyboards: list[tuple[int, int]] = []

    async def edit_message_reply_markup(
        self, *, chat_id: int, message_id: int, reply_markup: object | None = None
    ) -> None:
        if self.fail_edit:
            raise TelegramBadRequest(method=EditMessageReplyMarkup(), message="edit failed")
        assert reply_markup is None
        self.removed_keyboards.append((chat_id, message_id))


class FakeMessage:
    def __init__(
        self, text: str | None = None, *, bot: FakeBot | None = None, message_id: int = 1
    ) -> None:
        self.text = text
        self.message_id = message_id
        self.answers: list[tuple[str, object | None]] = []
        self.from_user = SimpleNamespace(id=123, first_name="Анна")
        self.chat = SimpleNamespace(id=456)
        self.bot = bot

    async def answer(self, text: str, reply_markup: object | None = None) -> SimpleNamespace:
        self.answers.append((text, reply_markup))
        return SimpleNamespace(message_id=len(self.answers))


class FakeCallback:
    def __init__(self, data: str, message: FakeMessage) -> None:
        self.data = data
        self.message = message
        self.answered = False

    async def answer(self) -> None:
        self.answered = True


def saved_profile() -> dict[str, object]:
    return {
        "user_id": 7,
        "target_roles": ["Backend Engineer"],
        "skills": ["Python", "FastAPI"],
        "experience": "middle",
        "location": ["Yerevan"],
        "workplace_preference": "remote",
        "salary_min": "2500.00",
        "salary_currency": "USD",
        "salary_period": "month",
        "languages": [{"language": "English", "level": "B2"}],
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
    }


class FakeApiClient:
    def __init__(
        self,
        profile: dict[str, object] | None = None,
        error: httpx.HTTPError | None = None,
    ) -> None:
        self.profile = profile
        self.error = error
        self.get_calls: list[int] = []

    async def create_or_get_user(self, telegram_user: object) -> int:
        if self.error is not None:
            raise self.error
        return 7

    async def get_user_profile(self, user_id: int) -> dict[str, object] | None:
        self.get_calls.append(user_id)
        if self.error is not None:
            raise self.error
        return self.profile

    async def normalize_profile_skills(self, skills: list[str]) -> list[str]:
        return skills

    async def normalize_profile_languages(
        self, languages: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        return languages


def make_state() -> tuple[MemoryStorage, FSMContext]:
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=456, user_id=123))
    return storage, state


def make_update(text: str, *, update_id: int, command: bool = False) -> Update:
    entities = [MessageEntity(type="bot_command", offset=0, length=len(text))] if command else []
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(),
            chat=Chat(id=456, type="private"),
            from_user=User(id=123, is_bot=False, first_name="Анна"),
            text=text,
            entities=entities,
        ),
    )


def make_profile_callback_update(
    callback_data: str, *, update_id: int, message_id: int
) -> Update:
    telegram_user = User(id=123, is_bot=False, first_name="Анна")
    message = Message(
        message_id=message_id,
        date=datetime.now(),
        chat=Chat(id=456, type="private"),
        from_user=telegram_user,
        text="profile",
    )
    callback = CallbackQuery(
        id=f"callback-{update_id}",
        from_user=telegram_user,
        chat_instance="chat-instance",
        data=callback_data,
        message=message,
    )
    return Update(update_id=update_id, callback_query=callback)


async def set_dispatcher_state(bot: Bot, state: str | None, data: dict[str, object] | None = None) -> FSMContext:
    await dp.fsm.events_isolation.close()
    context = FSMContext(
        storage=dp.storage,
        key=StorageKey(bot_id=bot.id, chat_id=456, user_id=123),
    )
    await context.set_state(state)
    await context.set_data(data or {})
    return context


def test_dispatcher_routes_active_field_edit_back_to_picker(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:menu-test-token")
        answers: list[str] = []
        removed_message_ids: list[int] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            answers.append(text)
            return SimpleNamespace(message_id=30 + len(answers))

        async def edit_reply_markup(message: Message, **kwargs: object) -> None:
            assert kwargs == {"reply_markup": None}
            removed_message_ids.append(message.message_id)

        async def callback_answer(callback: CallbackQuery, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Message, "edit_reply_markup", edit_reply_markup)
        monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
        state = await set_dispatcher_state(
            bot,
            ProfileSetupStates.edit_field.state,
            {
                "target_roles": ["Engineer"],
                "skills": ["Python", "PostgreSQL"],
                PROFILE_DRAFT_SOURCE: "persisted",
                PROFILE_EDITING_FIELD: "skills",
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 17,
            },
        )

        await dp.feed_update(bot, make_profile_callback_update("profile:edit_back", update_id=701, message_id=17))

        data = await state.get_data()
        assert data["skills"] == ["Python", "PostgreSQL"]
        assert PROFILE_EDITING_FIELD not in data
        assert data[ACTIVE_PROFILE_PROMPT_MESSAGE_ID] == 31
        assert await state.get_state() == ProfileSetupStates.edit_field.state
        assert answers == ["Какое поле изменить?"]
        assert removed_message_ids == [17]

        await dp.feed_update(bot, make_profile_callback_update("profile:edit_back", update_id=702, message_id=17))

        assert answers == ["Какое поле изменить?"]
        assert removed_message_ids == [17]
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_saved_card_callbacks_are_stale_after_editor_or_replacement_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:menu-test-token")
        answers: list[str] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            answers.append(text)
            return SimpleNamespace(message_id=30 + len(answers))

        async def callback_answer(callback: CallbackQuery, **kwargs: object) -> None:
            return None

        async def failed_cleanup(
            _bot: Bot, *, chat_id: int, message_id: int, reply_markup: object | None = None
        ) -> None:
            raise TelegramBadRequest(method=EditMessageReplyMarkup(), message="cleanup failed")

        async def edit_reply_markup(message: Message, **kwargs: object) -> None:
            assert kwargs == {"reply_markup": None}

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Message, "edit_reply_markup", edit_reply_markup)
        monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", failed_cleanup)

        snapshot = saved_profile()
        state = await set_dispatcher_state(
            bot,
            None,
            {PROFILE_SECTION_MESSAGE_ID: 10, PERSISTED_PROFILE_SNAPSHOT: snapshot},
        )
        await dp.feed_update(
            bot, make_profile_callback_update(PROFILE_SECTION_EDIT_CALLBACK, update_id=710, message_id=10)
        )
        await state.update_data(skills=["Unsaved draft skill"])
        await dp.feed_update(
            bot, make_profile_callback_update(PROFILE_SECTION_EDIT_CALLBACK, update_id=711, message_id=10)
        )

        assert await state.get_state() == ProfileSetupStates.edit_field.state
        assert (await state.get_data())["skills"] == ["Unsaved draft skill"]
        assert answers == ["Какое поле изменить?"]

        await state.set_state(None)
        await state.set_data({PROFILE_SECTION_MESSAGE_ID: 10, PERSISTED_PROFILE_SNAPSHOT: snapshot})
        answers.clear()
        await dp.feed_update(
            bot,
            make_profile_callback_update(
                PROFILE_SECTION_REPLACE_CV_CALLBACK, update_id=712, message_id=10
            ),
        )
        warning_id = (await state.get_data())[ACTIVE_PROFILE_PROMPT_MESSAGE_ID]
        replacement_data = await state.get_data()
        await dp.feed_update(
            bot,
            make_profile_callback_update(
                PROFILE_SECTION_REPLACE_CV_CALLBACK, update_id=713, message_id=10
            ),
        )
        await dp.feed_update(
            bot, make_profile_callback_update(PROFILE_SECTION_EDIT_CALLBACK, update_id=714, message_id=10)
        )

        assert await state.get_state() == ProfileSetupStates.cv_replace_warning.state
        assert await state.get_data() == replacement_data
        assert len(answers) == 1

        await dp.feed_update(
            bot,
            make_profile_callback_update(
                PROFILE_REPLACE_CV_CONTINUE_CALLBACK, update_id=715, message_id=warning_id
            ),
        )
        waiting_data = await state.get_data()
        await dp.feed_update(
            bot, make_profile_callback_update(PROFILE_SECTION_EDIT_CALLBACK, update_id=716, message_id=10)
        )
        await dp.feed_update(
            bot,
            make_profile_callback_update(
                PROFILE_SECTION_REPLACE_CV_CALLBACK, update_id=717, message_id=10
            ),
        )

        assert await state.get_state() == ProfileSetupStates.cv_waiting_document.state
        assert (await state.get_data()) == waiting_data
        assert waiting_data[PROFILE_DRAFT_SOURCE] == CV_REPLACEMENT_DRAFT_SOURCE
        assert waiting_data[PERSISTED_PROFILE_SNAPSHOT] == profile_payload(snapshot)
        await bot.session.close()

    asyncio.run(scenario())


def test_menu_add_job_clears_active_profile_flow_and_removes_skip_keyboard() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        bot = FakeBot()
        await state.set_state(ProfileSetupStates.skills)
        await state.update_data(target_roles=["Engineer"], skip_prompt_message_id=12)
        message = FakeMessage(ADD_JOB_BUTTON, bot=bot)

        await main_menu_action(message, state)

        assert bot.removed_keyboards == [(456, 12)]
        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert await state.get_data() == {}
        assert message.answers == [(REQUEST_URL_MESSAGE, None)]
        await storage.close()

    asyncio.run(scenario())


def test_cleanup_failure_does_not_block_menu_transition() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.skills)
        await state.update_data(skip_prompt_message_id=12)
        message = FakeMessage(ADD_JOB_BUTTON, bot=FakeBot(fail_edit=True))

        await main_menu_action(message, state)

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert message.answers == [(REQUEST_URL_MESSAGE, None)]
        await storage.close()

    asyncio.run(scenario())


def test_menu_add_job_cleans_active_profile_enum_keyboard() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        bot = FakeBot()
        await state.set_state(ProfileSetupStates.experience)
        await state.update_data(**{ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 13})
        message = FakeMessage(ADD_JOB_BUTTON, bot=bot)

        await main_menu_action(message, state)

        assert bot.removed_keyboards == [(456, 13)]
        assert await state.get_state() == AddJobStates.waiting_for_url.state
        await storage.close()

    asyncio.run(scenario())


def test_menu_profile_renders_existing_saved_profile_without_starting_wizard() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(None)
        message = FakeMessage(PROFILE_BUTTON)

        api = FakeApiClient(saved_profile())
        await main_menu_action(message, state, api)

        assert await state.get_state() is None
        assert "🎯 Целевые роли: Backend Engineer" in message.answers[0][0]
        assert "🏠 Формат работы: Удалённо" in message.answers[0][0]
        assert isinstance(message.answers[0][1], InlineKeyboardMarkup)
        button = message.answers[0][1].inline_keyboard[0][0]
        assert button.text == "✏️ Изменить"
        assert button.callback_data == "profile_section:edit"
        assert api.get_calls == [7]
        await storage.close()

    asyncio.run(scenario())


def test_menu_profile_renders_missing_profile_actions() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage(PROFILE_BUTTON)

        await main_menu_action(message, state, FakeApiClient())

        assert await state.get_state() is None
        text, keyboard = message.answers[0]
        assert text == PROFILE_MISSING_MESSAGE
        assert [row[0].text for row in keyboard.inline_keyboard] == [
            "✏️ Заполнить вручную",
            "📄 Заполнить из CV",
        ]
        await storage.close()

    asyncio.run(scenario())


def test_saved_profile_card_renders_optional_empty_values_neutrally() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage(PROFILE_BUTTON)
        minimal = {
            "target_roles": ["Engineer"],
            "skills": [],
            "experience": "unknown",
            "location": [],
            "workplace_preference": "any",
            "salary_min": None,
            "salary_currency": None,
            "salary_period": "unknown",
            "languages": [],
        }

        await main_menu_action(message, state, FakeApiClient(minimal))

        card = message.answers[0][0]
        assert "🧩 Навыки: Не указаны" in card
        assert "📈 Опыт: Не указано" in card
        assert "📍 Локация: Не указаны" in card
        assert "🏠 Формат работы: Любой" in card
        assert "💰 Зарплата: Не указана" in card
        assert "🌍 Языки: Не указаны" in card
        await storage.close()

    asyncio.run(scenario())


def test_menu_profile_get_failure_is_not_treated_as_missing() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage(PROFILE_BUTTON)

        await main_menu_action(
            message, state, FakeApiClient(error=httpx.ReadTimeout("timed out"))
        )

        assert await state.get_state() is None
        assert message.answers == [(PROFILE_LOAD_ERROR_MESSAGE, None)]
        await storage.close()

    asyncio.run(scenario())


def test_menu_profile_invalid_response_shows_load_error_without_snapshot() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage(PROFILE_BUTTON)
        request = httpx.Request("GET", "http://api/users/7/profile")

        await main_menu_action(
            message,
            state,
            FakeApiClient(error=httpx.DecodingError("invalid profile", request=request)),
        )

        assert await state.get_state() is None
        assert await state.get_data() == {}
        assert message.answers == [(PROFILE_LOAD_ERROR_MESSAGE, None)]
        await storage.close()

    asyncio.run(scenario())


def test_profile_section_setup_button_starts_existing_profile_setup_flow() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(None)
        message = FakeMessage(message_id=12)
        callback = FakeCallback(PROFILE_SETUP_CALLBACK, message)
        await state.update_data(**{PROFILE_SECTION_MESSAGE_ID: 12})

        await profile_section_setup(callback, state)

        assert callback.answered is True
        assert await state.get_state() == ProfileSetupStates.target_roles.state
        assert message.answers == [(ROLE_PROMPT, None)]
        await storage.close()

    asyncio.run(scenario())


def test_profile_section_edit_loads_persisted_snapshot_into_generic_draft() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage(message_id=12, bot=FakeBot())
        await state.set_data(
            {
                PROFILE_SECTION_MESSAGE_ID: 12,
                PERSISTED_PROFILE_SNAPSHOT: saved_profile(),
            }
        )

        await profile_section_edit(
            FakeCallback(PROFILE_SECTION_EDIT_CALLBACK, message), state
        )

        data = await state.get_data()
        assert await state.get_state() == ProfileSetupStates.edit_field.state
        assert data[PROFILE_DRAFT_SOURCE] == "persisted"
        assert data[PROFILE_SECTION_MESSAGE_ID] == 12
        assert data["target_roles"] == ["Backend Engineer"]
        assert data["skills"] == ["Python", "FastAPI"]
        assert "user_id" not in data
        assert "updated_at" not in data
        assert message.bot.removed_keyboards == [(456, 12)]
        assert message.answers[-1][0] == "Какое поле изменить?"
        assert message.answers[-1][1].inline_keyboard[-1][0].text == "❌ Отменить"
        await storage.close()

    asyncio.run(scenario())


def test_stale_saved_profile_edit_callback_is_noop() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage(message_id=11, bot=FakeBot())
        await state.set_data(
            {
                PROFILE_SECTION_MESSAGE_ID: 12,
                PERSISTED_PROFILE_SNAPSHOT: saved_profile(),
            }
        )

        await profile_section_edit(
            FakeCallback(PROFILE_SECTION_EDIT_CALLBACK, message), state
        )

        assert await state.get_state() is None
        assert message.answers == []
        assert message.bot.removed_keyboards == []
        await storage.close()

    asyncio.run(scenario())


def test_saved_profile_edit_cleanup_failure_does_not_block_editor_transition() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage(message_id=12, bot=FakeBot(fail_edit=True))
        await state.set_data(
            {
                PROFILE_SECTION_MESSAGE_ID: 12,
                PERSISTED_PROFILE_SNAPSHOT: saved_profile(),
            }
        )

        await profile_section_edit(
            FakeCallback(PROFILE_SECTION_EDIT_CALLBACK, message), state
        )

        assert await state.get_state() == ProfileSetupStates.edit_field.state
        assert message.answers[-1][0] == "Какое поле изменить?"
        await storage.close()

    asyncio.run(scenario())


def test_stale_profile_section_callback_does_not_start_profile_setup() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage(message_id=12)

        await profile_section_setup(FakeCallback(PROFILE_SETUP_CALLBACK, message), state)

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert message.answers == []
        await storage.close()

    asyncio.run(scenario())


def test_regular_fsm_text_is_not_a_menu_action() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.skills)
        message = FakeMessage("Python, FastAPI")

        await handle_skills(message, state, FakeApiClient())

        assert await state.get_state() == ProfileSetupStates.experience.state
        assert message.answers[0][0] != REQUEST_URL_MESSAGE
        await storage.close()

    asyncio.run(scenario())


def test_slash_command_handlers_continue_to_start_existing_flows() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage()

        await add_job(message, state)
        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert message.answers == [(REQUEST_URL_MESSAGE, None)]

        message.answers.clear()
        await profile_setup(message, state)
        assert await state.get_state() == ProfileSetupStates.target_roles.state
        assert message.answers == [(ROLE_PROMPT, None)]

        message.answers.clear()
        await cancel(message, state)
        assert await state.get_state() is None
        assert message.answers == [(PROFILE_CANCELLED_MESSAGE, None)]
        await storage.close()

    asyncio.run(scenario())


def test_dispatcher_routes_regular_text_to_profile_fsm(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:menu-test-token")
        answers: list[str] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            answers.append(text)
            return SimpleNamespace(message_id=len(answers))

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(main_module, "api_client", FakeApiClient())
        state = await set_dispatcher_state(bot, ProfileSetupStates.skills.state)

        await dp.feed_update(bot, make_update("Python, FastAPI", update_id=1))

        assert await state.get_state() == ProfileSetupStates.experience.state
        assert answers == ["Какой у тебя уровень опыта?"]
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_prioritizes_menu_add_job_over_profile_fsm(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:menu-test-token")
        answers: list[str] = []
        removed_keyboards: list[tuple[int, int]] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            answers.append(text)
            return SimpleNamespace(message_id=len(answers))

        async def edit_message_reply_markup(
            bot: Bot, *, chat_id: int, message_id: int, reply_markup: object | None = None
        ) -> None:
            assert reply_markup is None
            removed_keyboards.append((chat_id, message_id))

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_message_reply_markup)
        state = await set_dispatcher_state(
            bot,
            ProfileSetupStates.skills.state,
            {"target_roles": ["Engineer"], "skip_prompt_message_id": 12},
        )

        await dp.feed_update(bot, make_update(ADD_JOB_BUTTON, update_id=2))

        assert removed_keyboards == [(456, 12)]
        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert await state.get_data() == {}
        assert answers == [REQUEST_URL_MESSAGE]
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_prioritizes_menu_profile_over_add_job_fsm(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:menu-test-token")
        answers: list[tuple[str, object | None]] = []

        async def answer(
            message: Message, text: str, reply_markup: object | None = None, **kwargs: object
        ) -> SimpleNamespace:
            answers.append((text, reply_markup))
            return SimpleNamespace(message_id=len(answers))

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(main_module, "api_client", FakeApiClient())
        state = await set_dispatcher_state(bot, AddJobStates.waiting_for_url.state)

        await dp.feed_update(bot, make_update(PROFILE_BUTTON, update_id=3))

        assert await state.get_state() is None
        assert answers[0][0] == PROFILE_MISSING_MESSAGE
        assert isinstance(answers[0][1], InlineKeyboardMarkup)
        await bot.session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "profile_state",
    [
        ProfileSetupStates.skills.state,
        ProfileSetupStates.cv_waiting_document.state,
        ProfileSetupStates.cv_processing.state,
    ],
)
def test_dispatcher_profile_menu_replaces_manual_or_cv_flow(
    monkeypatch, profile_state: str
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:menu-test-token")
        answers: list[str] = []
        removed_keyboards: list[tuple[int, int]] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            answers.append(text)
            return SimpleNamespace(message_id=len(answers))

        async def edit_message_reply_markup(
            bot: Bot, *, chat_id: int, message_id: int, reply_markup: object | None = None
        ) -> None:
            assert reply_markup is None
            removed_keyboards.append((chat_id, message_id))

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_message_reply_markup)
        monkeypatch.setattr(main_module, "api_client", FakeApiClient())
        state = await set_dispatcher_state(
            bot, profile_state, {ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 22}
        )

        await dp.feed_update(bot, make_update(PROFILE_BUTTON, update_id=30))

        assert await state.get_state() is None
        assert answers == [PROFILE_MISSING_MESSAGE]
        assert removed_keyboards == [(456, 22)]
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_routes_existing_commands(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:menu-test-token")
        answers: list[str] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            answers.append(text)
            return SimpleNamespace(message_id=len(answers))

        monkeypatch.setattr(Message, "answer", answer)
        state = await set_dispatcher_state(bot, None)

        await dp.feed_update(bot, make_update("/cancel", update_id=4, command=True))
        assert await state.get_state() is None
        assert answers == [NO_ACTIVE_FLOW_MESSAGE]

        answers.clear()
        await dp.feed_update(bot, make_update("/add_job", update_id=5, command=True))
        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert answers == [REQUEST_URL_MESSAGE]

        answers.clear()
        await dp.feed_update(bot, make_update("/profile_setup", update_id=6, command=True))
        assert await state.get_state() == ProfileSetupStates.target_roles.state
        assert answers == [ROLE_PROMPT]

        answers.clear()
        await dp.feed_update(bot, make_update("/cancel", update_id=7, command=True))
        assert await state.get_state() is None
        assert answers == [PROFILE_CANCELLED_MESSAGE]
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_cleans_profile_section_before_starting_add_job(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:menu-test-token")
        answers: list[tuple[str, object | None]] = []
        removed_keyboards: list[tuple[int, int]] = []

        async def answer(
            message: Message, text: str, reply_markup: object | None = None, **kwargs: object
        ) -> SimpleNamespace:
            answers.append((text, reply_markup))
            return SimpleNamespace(message_id=len(answers))

        async def edit_message_reply_markup(
            bot: Bot, *, chat_id: int, message_id: int, reply_markup: object | None = None
        ) -> None:
            assert reply_markup is None
            removed_keyboards.append((chat_id, message_id))

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_message_reply_markup)
        monkeypatch.setattr(main_module, "api_client", FakeApiClient())
        state = await set_dispatcher_state(bot, None)

        await dp.feed_update(bot, make_update(PROFILE_BUTTON, update_id=7))
        assert (await state.get_data())[PROFILE_SECTION_MESSAGE_ID] == 1
        await dp.feed_update(bot, make_update(ADD_JOB_BUTTON, update_id=8))

        assert removed_keyboards == [(456, 1)]
        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert answers[-1][0] == REQUEST_URL_MESSAGE
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_section_cleanup_failure_does_not_block_add_job(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:menu-test-token")
        answers: list[str] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            answers.append(text)
            return SimpleNamespace(message_id=len(answers))

        async def edit_message_reply_markup(
            bot: Bot, **kwargs: object
        ) -> None:
            raise TelegramBadRequest(method=EditMessageReplyMarkup(), message="edit failed")

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_message_reply_markup)
        state = await set_dispatcher_state(bot, None, {PROFILE_SECTION_MESSAGE_ID: 12})

        await dp.feed_update(bot, make_update(ADD_JOB_BUTTON, update_id=9))

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert answers == [REQUEST_URL_MESSAGE]
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_replaces_profile_section_after_navigation(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:menu-test-token")
        answers: list[tuple[str, object | None]] = []
        removed_keyboards: list[tuple[int, int]] = []

        async def answer(
            message: Message, text: str, reply_markup: object | None = None, **kwargs: object
        ) -> SimpleNamespace:
            answers.append((text, reply_markup))
            return SimpleNamespace(message_id=len(answers))

        async def edit_message_reply_markup(
            bot: Bot, *, chat_id: int, message_id: int, reply_markup: object | None = None
        ) -> None:
            removed_keyboards.append((chat_id, message_id))

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_message_reply_markup)
        monkeypatch.setattr(main_module, "api_client", FakeApiClient())
        state = await set_dispatcher_state(bot, None)

        await dp.feed_update(bot, make_update(PROFILE_BUTTON, update_id=10))
        await dp.feed_update(bot, make_update(ADD_JOB_BUTTON, update_id=11))
        await dp.feed_update(bot, make_update(PROFILE_BUTTON, update_id=12))

        assert removed_keyboards == [(456, 1)]
        assert (await state.get_data())[PROFILE_SECTION_MESSAGE_ID] == 3
        section_text, section_keyboard = answers[-1]
        assert section_text == PROFILE_MISSING_MESSAGE
        assert isinstance(section_keyboard, InlineKeyboardMarkup)
        assert section_keyboard.inline_keyboard[0][0].callback_data == PROFILE_SETUP_CALLBACK
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_serializes_double_profile_save(monkeypatch) -> None:
    async def scenario() -> None:
        class BlockingSaveApi(FakeApiClient):
            def __init__(self) -> None:
                super().__init__()
                self.put_started = asyncio.Event()
                self.release_put = asyncio.Event()
                self.put_calls: list[dict[str, object]] = []

            async def put_user_profile(
                self, user_id: int, profile: dict[str, object]
            ) -> dict[str, object]:
                self.put_calls.append(profile)
                self.put_started.set()
                await self.release_put.wait()
                return profile

        bot = Bot("123456:menu-test-token")
        answers = 0

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            nonlocal answers
            answers += 1
            return SimpleNamespace(message_id=900 + answers)

        async def callback_answer(callback: CallbackQuery, **kwargs: object) -> None:
            return None

        async def edit_reply_markup(message: Message, **kwargs: object) -> None:
            return None

        api = BlockingSaveApi()
        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
        monkeypatch.setattr(Message, "edit_reply_markup", edit_reply_markup)
        assert isinstance(dp.fsm.events_isolation, SimpleEventIsolation)

        draft = profile_payload(saved_profile())
        state = await set_dispatcher_state(
            bot,
            ProfileSetupStates.summary.state,
            {
                **draft,
                PROFILE_DRAFT_SOURCE: "persisted",
                PERSISTED_PROFILE_SNAPSHOT: draft,
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 40,
            },
        )
        first = asyncio.create_task(
            dp.feed_update(bot, make_profile_callback_update("profile:save", update_id=101, message_id=40))
        )
        await api.put_started.wait()
        second = asyncio.create_task(
            dp.feed_update(bot, make_profile_callback_update("profile:save", update_id=102, message_id=40))
        )
        await asyncio.sleep(0)

        assert len(api.put_calls) == 1
        assert not second.done()

        api.release_put.set()
        await asyncio.gather(first, second)

        assert len(api.put_calls) == 1
        assert await state.get_state() is None
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_persisted_save_makes_new_card_the_only_active_context(monkeypatch) -> None:
    async def scenario() -> None:
        class PersistedSaveApi(FakeApiClient):
            def __init__(self) -> None:
                super().__init__()
                self.put_calls: list[dict[str, object]] = []

            async def put_user_profile(
                self, user_id: int, profile: dict[str, object]
            ) -> dict[str, object]:
                self.put_calls.append(profile)
                return {**profile, "skills": ["Python", "FastAPI", "SQL"]}

        bot = Bot("123456:menu-test-token")
        removed_summary_ids: list[int] = []
        deleted_summary_ids: list[int] = []
        removed_section_ids: list[int] = []
        deleted_section_ids: list[int] = []
        answers: list[tuple[str, InlineKeyboardMarkup | None]] = []

        async def edit_reply_markup(message: Message, **kwargs: object) -> None:
            removed_summary_ids.append(message.message_id)

        async def delete(message: Message) -> None:
            deleted_summary_ids.append(message.message_id)

        async def edit_message_reply_markup(
            bot: Bot, *, chat_id: int, message_id: int, reply_markup: object | None = None
        ) -> None:
            assert reply_markup is None
            removed_section_ids.append(message_id)

        async def delete_message(bot: Bot, *, chat_id: int, message_id: int) -> None:
            deleted_section_ids.append(message_id)

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            reply_markup = kwargs.get("reply_markup")
            assert reply_markup is None or isinstance(reply_markup, InlineKeyboardMarkup)
            answers.append((text, reply_markup))
            return SimpleNamespace(message_id=12)

        async def callback_answer(callback: CallbackQuery, **kwargs: object) -> None:
            return None

        api = PersistedSaveApi()
        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_message_reply_markup)
        monkeypatch.setattr(Bot, "delete_message", delete_message)
        monkeypatch.setattr(Message, "edit_reply_markup", edit_reply_markup)
        monkeypatch.setattr(Message, "delete", delete)
        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(CallbackQuery, "answer", callback_answer)

        draft = profile_payload(saved_profile())
        state = await set_dispatcher_state(
            bot,
            ProfileSetupStates.summary.state,
            {
                **draft,
                PROFILE_DRAFT_SOURCE: "persisted",
                PERSISTED_PROFILE_SNAPSHOT: draft,
                PROFILE_SECTION_MESSAGE_ID: 10,
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 11,
            },
        )

        await dp.feed_update(bot, make_profile_callback_update("profile:save", update_id=401, message_id=11))

        assert api.put_calls == [draft]
        assert removed_summary_ids == []
        assert deleted_summary_ids == [11]
        assert deleted_section_ids == [10]
        assert removed_section_ids == []
        assert len(answers) == 1
        text, keyboard = answers[0]
        assert "🧩 Навыки: Python, FastAPI, SQL" in text
        assert keyboard is not None
        assert keyboard.inline_keyboard[0][0].callback_data == PROFILE_SECTION_EDIT_CALLBACK
        assert (await state.get_data())[PROFILE_SECTION_MESSAGE_ID] == 12

        await dp.feed_update(
            bot,
            make_profile_callback_update(PROFILE_SECTION_EDIT_CALLBACK, update_id=402, message_id=10),
        )

        assert await state.get_state() is None
        assert answers == [(text, keyboard)]

        await dp.feed_update(
            bot,
            make_profile_callback_update(PROFILE_SECTION_EDIT_CALLBACK, update_id=403, message_id=12),
        )

        assert removed_section_ids == [12]
        assert await state.get_state() == ProfileSetupStates.edit_field.state
        assert answers[-1][0] == "Какое поле изменить?"

        await dp.feed_update(bot, make_profile_callback_update("profile:save", update_id=404, message_id=11))

        assert api.put_calls == [draft]
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_persisted_save_continues_when_summary_delete_fails(monkeypatch) -> None:
    async def scenario() -> None:
        class PersistedSaveApi(FakeApiClient):
            def __init__(self) -> None:
                super().__init__()
                self.put_calls: list[dict[str, object]] = []

            async def put_user_profile(
                self, user_id: int, profile: dict[str, object]
            ) -> dict[str, object]:
                self.put_calls.append(profile)
                return profile

        bot = Bot("123456:menu-test-token")
        deleted_section_ids: list[int] = []
        removed_section_ids: list[int] = []
        deleted_summary_ids: list[int] = []
        answers: list[tuple[str, InlineKeyboardMarkup | None]] = []

        async def delete_message(bot: Bot, *, chat_id: int, message_id: int) -> None:
            deleted_section_ids.append(message_id)

        async def edit_message_reply_markup(
            bot: Bot, *, chat_id: int, message_id: int, reply_markup: object | None = None
        ) -> None:
            assert reply_markup is None
            removed_section_ids.append(message_id)

        async def edit_reply_markup(message: Message, **kwargs: object) -> None:
            return None

        async def delete(message: Message) -> None:
            deleted_summary_ids.append(message.message_id)
            raise TelegramBadRequest(method=EditMessageReplyMarkup(), message="delete failed")

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            reply_markup = kwargs.get("reply_markup")
            assert reply_markup is None or isinstance(reply_markup, InlineKeyboardMarkup)
            answers.append((text, reply_markup))
            return SimpleNamespace(message_id=12)

        async def callback_answer(callback: CallbackQuery, **kwargs: object) -> None:
            return None

        api = PersistedSaveApi()
        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Bot, "delete_message", delete_message)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_message_reply_markup)
        monkeypatch.setattr(Message, "edit_reply_markup", edit_reply_markup)
        monkeypatch.setattr(Message, "delete", delete)
        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(CallbackQuery, "answer", callback_answer)

        draft = profile_payload(saved_profile())
        state = await set_dispatcher_state(
            bot,
            ProfileSetupStates.summary.state,
            {
                **draft,
                PROFILE_DRAFT_SOURCE: "persisted",
                PERSISTED_PROFILE_SNAPSHOT: draft,
                PROFILE_SECTION_MESSAGE_ID: 10,
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 11,
            },
        )

        await dp.feed_update(bot, make_profile_callback_update("profile:save", update_id=411, message_id=11))

        assert api.put_calls == [draft]
        assert deleted_section_ids == [10]
        assert removed_section_ids == []
        assert deleted_summary_ids == [11]
        assert len(answers) == 1
        assert answers[0][1].inline_keyboard[0][0].callback_data == PROFILE_SECTION_EDIT_CALLBACK
        assert (await state.get_data())[PROFILE_SECTION_MESSAGE_ID] == 12

        await dp.feed_update(
            bot,
            make_profile_callback_update(PROFILE_SECTION_EDIT_CALLBACK, update_id=412, message_id=10),
        )

        assert await state.get_state() is None
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_persisted_cancel_replaces_summary_with_one_saved_card(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:menu-test-token")
        deleted_section_ids: list[int] = []
        deleted_summary_ids: list[int] = []
        answers: list[tuple[str, InlineKeyboardMarkup | None]] = []

        async def delete_message(bot: Bot, *, chat_id: int, message_id: int) -> None:
            deleted_section_ids.append(message_id)

        async def delete(message: Message) -> None:
            deleted_summary_ids.append(message.message_id)

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            reply_markup = kwargs.get("reply_markup")
            assert reply_markup is None or isinstance(reply_markup, InlineKeyboardMarkup)
            answers.append((text, reply_markup))
            return SimpleNamespace(message_id=12)

        async def callback_answer(callback: CallbackQuery, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(main_module, "api_client", FakeApiClient())
        monkeypatch.setattr(Bot, "delete_message", delete_message)
        monkeypatch.setattr(Message, "delete", delete)
        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(CallbackQuery, "answer", callback_answer)

        original = profile_payload(saved_profile())
        state = await set_dispatcher_state(
            bot,
            ProfileSetupStates.summary.state,
            {
                **original,
                "skills": ["Draft-only skill"],
                PROFILE_DRAFT_SOURCE: "persisted",
                PERSISTED_PROFILE_SNAPSHOT: original,
                PROFILE_SECTION_MESSAGE_ID: 10,
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 11,
            },
        )

        await dp.feed_update(bot, make_profile_callback_update("profile:cancel", update_id=421, message_id=11))

        assert deleted_section_ids == [10]
        assert deleted_summary_ids == [11]
        assert len(answers) == 1
        text, keyboard = answers[0]
        assert "🧩 Навыки: Python, FastAPI" in text
        assert "Draft-only skill" not in text
        assert keyboard is not None
        assert keyboard.inline_keyboard[0][0].callback_data == PROFILE_SECTION_EDIT_CALLBACK
        assert (await state.get_data())[PROFILE_SECTION_MESSAGE_ID] == 12
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_navigation_waits_for_inflight_profile_get(monkeypatch) -> None:
    async def scenario() -> None:
        class BlockingGetApi(FakeApiClient):
            def __init__(self) -> None:
                super().__init__()
                self.get_started = asyncio.Event()
                self.release_get = asyncio.Event()

            async def get_user_profile(self, user_id: int) -> dict[str, object] | None:
                self.get_started.set()
                await self.release_get.wait()
                return None

        bot = Bot("123456:menu-test-token")
        answers: list[str] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            answers.append(text)
            return SimpleNamespace(message_id=len(answers))

        api = BlockingGetApi()
        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "answer", answer)
        state = await set_dispatcher_state(bot, None)

        profile_task = asyncio.create_task(
            dp.feed_update(bot, make_update(PROFILE_BUTTON, update_id=201))
        )
        await api.get_started.wait()
        navigation_task = asyncio.create_task(
            dp.feed_update(bot, make_update(ADD_JOB_BUTTON, update_id=202))
        )
        await asyncio.sleep(0)

        assert not navigation_task.done()
        api.release_get.set()
        await asyncio.gather(profile_task, navigation_task)

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert answers[-1] == REQUEST_URL_MESSAGE
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_navigation_waits_for_inflight_profile_put(monkeypatch) -> None:
    async def scenario() -> None:
        class BlockingSaveApi(FakeApiClient):
            def __init__(self) -> None:
                super().__init__()
                self.put_started = asyncio.Event()
                self.release_put = asyncio.Event()

            async def put_user_profile(
                self, user_id: int, profile: dict[str, object]
            ) -> dict[str, object]:
                self.put_started.set()
                await self.release_put.wait()
                return profile

        bot = Bot("123456:menu-test-token")
        answers: list[str] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            answers.append(text)
            return SimpleNamespace(message_id=len(answers))

        async def callback_answer(callback: CallbackQuery, **kwargs: object) -> None:
            return None

        async def edit_reply_markup(message: Message, **kwargs: object) -> None:
            return None

        api = BlockingSaveApi()
        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
        monkeypatch.setattr(Message, "edit_reply_markup", edit_reply_markup)
        draft = profile_payload(saved_profile())
        state = await set_dispatcher_state(
            bot,
            ProfileSetupStates.summary.state,
            {
                **draft,
                PROFILE_DRAFT_SOURCE: "persisted",
                PERSISTED_PROFILE_SNAPSHOT: draft,
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 50,
            },
        )

        save_task = asyncio.create_task(
            dp.feed_update(bot, make_profile_callback_update("profile:save", update_id=301, message_id=50))
        )
        await api.put_started.wait()
        navigation_task = asyncio.create_task(
            dp.feed_update(bot, make_update(ADD_JOB_BUTTON, update_id=302))
        )
        await asyncio.sleep(0)

        assert not navigation_task.done()
        api.release_put.set()
        await asyncio.gather(save_task, navigation_task)

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert answers[-1] == REQUEST_URL_MESSAGE
        await bot.session.close()

    asyncio.run(scenario())
