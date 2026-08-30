import asyncio
import os
from datetime import datetime
from types import SimpleNamespace

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageReplyMarkup
from aiogram.types import Chat, InlineKeyboardMarkup, Message, MessageEntity, Update, User

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:menu-test-token")

from app.jobs import NO_ACTIVE_FLOW_MESSAGE, REQUEST_URL_MESSAGE, AddJobStates
from app.menu import (
    ADD_JOB_BUTTON,
    PROFILE_BUTTON,
    PROFILE_SECTION_MESSAGE,
    PROFILE_SECTION_MESSAGE_ID,
    PROFILE_SETUP_CALLBACK,
    main_menu_action,
    profile_section_setup,
)
from app.main import add_job, cancel, dp, profile_setup
from app.profile import (
    ACTIVE_PROFILE_PROMPT_MESSAGE_ID,
    PROFILE_CANCELLED_MESSAGE,
    ProfileSetupStates,
    ROLE_PROMPT,
    handle_skills,
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


async def set_dispatcher_state(bot: Bot, state: str | None, data: dict[str, object] | None = None) -> FSMContext:
    context = FSMContext(
        storage=dp.storage,
        key=StorageKey(bot_id=bot.id, chat_id=456, user_id=123),
    )
    await context.set_state(state)
    await context.set_data(data or {})
    return context


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


def test_menu_profile_opens_section_without_starting_wizard() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage(PROFILE_BUTTON)

        await main_menu_action(message, state)

        assert await state.get_state() is None
        assert message.answers[0][0] == PROFILE_SECTION_MESSAGE
        assert isinstance(message.answers[0][1], InlineKeyboardMarkup)
        button = message.answers[0][1].inline_keyboard[0][0]
        assert button.text == "✏️ Заполнить / изменить профиль"
        assert button.callback_data == PROFILE_SETUP_CALLBACK
        await storage.close()

    asyncio.run(scenario())


def test_profile_section_setup_button_starts_existing_profile_setup_flow() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(AddJobStates.waiting_for_url)
        message = FakeMessage(message_id=12)
        callback = FakeCallback(PROFILE_SETUP_CALLBACK, message)
        await state.update_data(**{PROFILE_SECTION_MESSAGE_ID: 12})

        await profile_section_setup(callback, state)

        assert callback.answered is True
        assert await state.get_state() == ProfileSetupStates.target_roles.state
        assert message.answers == [(ROLE_PROMPT, None)]
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

        await handle_skills(message, state)

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
        state = await set_dispatcher_state(bot, AddJobStates.waiting_for_url.state)

        await dp.feed_update(bot, make_update(PROFILE_BUTTON, update_id=3))

        assert await state.get_state() is None
        assert answers[0][0] == PROFILE_SECTION_MESSAGE
        assert isinstance(answers[0][1], InlineKeyboardMarkup)
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
        state = await set_dispatcher_state(bot, None)

        await dp.feed_update(bot, make_update(PROFILE_BUTTON, update_id=10))
        await dp.feed_update(bot, make_update(ADD_JOB_BUTTON, update_id=11))
        await dp.feed_update(bot, make_update(PROFILE_BUTTON, update_id=12))

        assert removed_keyboards == [(456, 1)]
        assert (await state.get_data())[PROFILE_SECTION_MESSAGE_ID] == 3
        section_text, section_keyboard = answers[-1]
        assert section_text == PROFILE_SECTION_MESSAGE
        assert isinstance(section_keyboard, InlineKeyboardMarkup)
        assert section_keyboard.inline_keyboard[0][0].callback_data == PROFILE_SETUP_CALLBACK
        await bot.session.close()

    asyncio.run(scenario())
