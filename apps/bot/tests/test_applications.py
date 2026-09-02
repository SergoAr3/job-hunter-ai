import asyncio
import os
from datetime import datetime
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageReplyMarkup, EditMessageText
from aiogram.types import CallbackQuery, Chat, Message as TelegramMessage, Update, User

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:applications-test-token")

import app.main as main_module
from app.applications import (
    APPLICATIONS_MESSAGE_ID,
    APPLICATIONS_OFFSET,
    PAGE_SIZE,
    _replace_or_send,
    handle_applications_callback,
    show_applications_list,
)
from app.jobs import ACTIVE_MATCH_DETAILS_CLAIM_ID, ACTIVE_MATCH_MESSAGE_ID, MATCH_DETAILS_PREFIX


class Message:
    next_id = 10

    def __init__(self) -> None:
        self.message_id = Message.next_id
        Message.next_id += 1
        self.from_user = SimpleNamespace(id=1)
        self.chat = SimpleNamespace(id=2)
        self.answers: list[Message] = []
        self.text = ""
        self.reply_markup = None

    async def answer(self, text: str, reply_markup: object = None) -> "Message":
        result = Message()
        result.from_user = SimpleNamespace(id=999, is_bot=True)
        result.text, result.reply_markup = text, reply_markup
        self.answers.append(result)
        return result

    async def edit_text(self, text: str, reply_markup: object = None) -> None:
        self.text, self.reply_markup = text, reply_markup


class Callback:
    def __init__(self, data: str, message: Message, actor: object | None = None) -> None:
        self.data, self.message = data, message
        self.from_user = actor or SimpleNamespace(id=1, is_bot=False)
        self.answered = False

    async def answer(self) -> None:
        self.answered = True


class Api:
    def __init__(self) -> None:
        self.pages: list[dict[str, object]] = []
        self.detail: dict[str, object] = {"application": {"id": 7}, "job": {"title": "Python Developer", "workplace_type": "remote"}}
        self.create_users: list[int] = []
        self.list_calls: list[int] = []
        self.detail_calls: list[tuple[int, int]] = []
        self.match_calls: list[tuple[int, int]] = []

    async def create_or_get_user(self, user: object) -> int:
        self.create_users.append(user.id)
        if user.id == 999:
            return 5
        return 1

    async def list_applications(self, user_id: int, *, limit: int, offset: int) -> dict[str, object]:
        assert limit == PAGE_SIZE
        self.list_calls.append(user_id)
        return self.pages[offset // PAGE_SIZE]

    async def get_application(self, user_id: int, app_id: int) -> dict[str, object]:
        self.detail_calls.append((user_id, app_id))
        return self.detail

    async def get_application_match(self, user_id: int, app_id: int) -> dict[str, object]:
        self.match_calls.append((user_id, app_id))
        return {"score": 80, "verdict": "high"}


def _state() -> tuple[MemoryStorage, FSMContext]:
    storage = MemoryStorage()
    return storage, FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=2, user_id=1))


def test_list_pagination_detail_and_stale_callback() -> None:
    async def scenario() -> None:
        storage, state = _state()
        api = Api()
        api.pages = [
            {"items": [{"app_id": 7, "title": "First", "company": "Acme", "location": "Yerevan", "workplace_type": "remote"}], "has_next": True},
            {"items": [{"app_id": 8, "title": "Second", "company": None, "location": None, "workplace_type": "onsite"}], "has_next": False},
        ]
        root = Message()
        await show_applications_list(root, state, api)
        canonical = root.answers[-1]
        assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == canonical.message_id
        await handle_applications_callback(Callback("applications:page:5", canonical), state, api)
        assert "Second" in canonical.text and (await state.get_data())[APPLICATIONS_OFFSET] == 5
        await handle_applications_callback(Callback("applications:open:8:5", canonical), state, api)
        assert "Python Developer" in canonical.text
        assert api.detail_calls == [(1, 8)]
        assert 999 not in api.create_users
        await handle_applications_callback(Callback("applications:page:0", canonical), state, api)
        assert api.list_calls[-1] == 1
        stale = Message()
        await handle_applications_callback(Callback("applications:page:0", stale), state, api)
        assert stale.text == ""
        await storage.close()
    asyncio.run(scenario())


def test_applications_edit_fallback_cleans_old_keyboard_before_replacing_message() -> None:
    class CleanupBot:
        def __init__(self, *, fail_cleanup: bool = False) -> None:
            self.fail_cleanup = fail_cleanup
            self.cleaned: list[tuple[int, int]] = []

        async def edit_message_reply_markup(
            self, *, chat_id: int, message_id: int, reply_markup: object | None = None
        ) -> None:
            assert reply_markup is None
            self.cleaned.append((chat_id, message_id))
            if self.fail_cleanup:
                raise TelegramBadRequest(
                    method=EditMessageReplyMarkup(), message="cleanup failed"
                )

    class FailingMessage(Message):
        def __init__(self, bot: CleanupBot) -> None:
            super().__init__()
            self.bot = bot

        async def edit_text(self, text: str, reply_markup: object = None) -> None:
            raise TelegramBadRequest(
                method=EditMessageText(text="replacement"), message="edit failed"
            )

    async def scenario(fail_cleanup: bool) -> None:
        storage, state = _state()
        bot = CleanupBot(fail_cleanup=fail_cleanup)
        old = FailingMessage(bot)
        await state.update_data({APPLICATIONS_MESSAGE_ID: old.message_id})

        await _replace_or_send(old, state, "replacement", None)

        new = old.answers[-1]
        assert bot.cleaned == [(2, old.message_id)]
        assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == new.message_id
        api = Api()
        await handle_applications_callback(Callback("applications:page:0", old), state, api)
        assert api.create_users == []
        await storage.close()

    asyncio.run(scenario(False))
    asyncio.run(scenario(True))


def _applications_callback_update(
    callback_data: str, *, update_id: int, message_id: int
) -> Update:
    human = User(id=123, is_bot=False, first_name="Anna")
    bot_author = User(id=999, is_bot=True, first_name="Bot")
    message = TelegramMessage(
        message_id=message_id,
        date=datetime.now(),
        chat=Chat(id=456, type="private"),
        from_user=bot_author,
        text="applications",
    )
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=f"applications-{update_id}",
            from_user=human,
            chat_instance="chat-instance",
            data=callback_data,
            message=message,
        ),
    )


class DispatcherApi(Api):
    async def create_or_get_user(self, user: object) -> int:
        self.create_users.append(user.id)
        if user.id == 999:
            return 5
        return 4


async def _dispatcher_state(bot: Bot, data: dict[str, object]) -> FSMContext:
    await main_module.dp.fsm.events_isolation.close()
    state = FSMContext(
        storage=main_module.dp.storage,
        key=StorageKey(bot_id=bot.id, chat_id=456, user_id=123),
    )
    await state.clear()
    await state.set_data(data)
    return state


def test_dispatcher_back_invalidates_match_context_and_uses_callback_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:applications-test-token")
        api = DispatcherApi()
        api.pages = [
            {"items": [{"app_id": 17, "title": "Earlier", "company": None, "location": None, "workplace_type": "remote"}], "has_next": True},
            {"items": [{"app_id": 18, "title": "Python", "company": None, "location": None, "workplace_type": "remote"}], "has_next": False},
        ]
        edits: list[tuple[int, int]] = []
        answers: list[tuple[str, int]] = []

        async def answer(message: TelegramMessage, text: str, **kwargs: object) -> SimpleNamespace:
            message_id = 20 + len(answers)
            answers.append((text, message_id))
            return SimpleNamespace(message_id=message_id)

        async def edit_text(message: TelegramMessage, text: str, **kwargs: object) -> None:
            return None

        async def edit_markup(
            _bot: Bot, *, chat_id: int, message_id: int, reply_markup: object | None = None
        ) -> None:
            assert reply_markup is None
            edits.append((chat_id, message_id))

        async def callback_answer(callback: CallbackQuery, **kwargs: object) -> bool:
            return True

        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(TelegramMessage, "answer", answer)
        monkeypatch.setattr(TelegramMessage, "edit_text", edit_text)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_markup)
        monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
        state = await _dispatcher_state(bot, {APPLICATIONS_MESSAGE_ID: 10})

        await main_module.dp.feed_update(bot, _applications_callback_update("applications:page:5", update_id=1, message_id=10))
        await main_module.dp.feed_update(bot, _applications_callback_update("applications:open:18:5", update_id=2, message_id=10))
        await main_module.dp.feed_update(bot, _applications_callback_update("applications:match:18", update_id=3, message_id=10))
        match_message_id = answers[-1][1]
        assert (await state.get_data())[ACTIVE_MATCH_MESSAGE_ID] == match_message_id

        await main_module.dp.feed_update(bot, _applications_callback_update("applications:page:5", update_id=4, message_id=10))
        data = await state.get_data()
        assert data[APPLICATIONS_MESSAGE_ID] == 10
        assert data.get(ACTIVE_MATCH_MESSAGE_ID) is None
        assert data.get(ACTIVE_MATCH_DETAILS_CLAIM_ID) is None
        assert edits == [(456, match_message_id)]

        await main_module.dp.feed_update(bot, _applications_callback_update(f"{MATCH_DETAILS_PREFIX}18", update_id=5, message_id=match_message_id))
        assert api.match_calls == [(4, 18)]
        assert api.detail_calls == [(4, 18)]
        assert api.list_calls == [4, 4]
        assert api.create_users == [123, 123, 123, 123]
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_second_application_match_replaces_previous_match_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:applications-test-token")
        api = DispatcherApi()
        edits: list[tuple[int, int]] = []
        answers = 0

        async def answer(message: TelegramMessage, text: str, **kwargs: object) -> SimpleNamespace:
            nonlocal answers
            answers += 1
            return SimpleNamespace(message_id=20 + answers)

        async def edit_markup(
            _bot: Bot, *, chat_id: int, message_id: int, reply_markup: object | None = None
        ) -> None:
            assert reply_markup is None
            edits.append((chat_id, message_id))

        async def callback_answer(callback: CallbackQuery, **kwargs: object) -> bool:
            return True

        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(TelegramMessage, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_markup)
        monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
        state = await _dispatcher_state(bot, {APPLICATIONS_MESSAGE_ID: 10})

        await main_module.dp.feed_update(bot, _applications_callback_update("applications:match:18", update_id=10, message_id=10))
        first_match_message_id = (await state.get_data())[ACTIVE_MATCH_MESSAGE_ID]
        await main_module.dp.feed_update(bot, _applications_callback_update("applications:match:18", update_id=11, message_id=10))

        assert edits == [(456, first_match_message_id)]
        assert (await state.get_data())[ACTIVE_MATCH_MESSAGE_ID] == 22
        assert api.create_users == [123, 123]
        assert api.match_calls == [(4, 18), (4, 18)]
        await bot.session.close()

    asyncio.run(scenario())
