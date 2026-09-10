import asyncio
import json
import os
from datetime import datetime

import httpx
import pytest
from aiogram import Bot
from aiogram.types import Message as TelegramMessage, CallbackQuery, Update, Chat, User
from aiogram.utils.chat_action import ChatActionSender

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:applications-test-token")
import app.main as main_module
from app.applications import (
    APPLICATIONS_MESSAGE_ID, APPLICATIONS_APPLICATION_ID, APPLICATIONS_VIEW,
    APPLICATIONS_DETAIL_VIEW, ApplicationsStates, handle_applications_callback,
    handle_letter_language_text,
)
from test_applications import Message, Callback, Api, _state, _dispatcher_state, _applications_callback_update
from app.api_client import JobHunterApiClient


class TypingBot:
    id = 1

    def __init__(self):
        self.actions = []

    async def send_chat_action(self, **kwargs):
        self.actions.append(kwargs["action"])


class LetterApi(Api):
    def __init__(self):
        super().__init__()
        self.languages = []
        self.fail = None

    async def normalize_cover_letter_language(self, text):
        if text == "Italiano":
            return "it"
        raise httpx.HTTPStatusError("invalid", request=httpx.Request("POST", "https://example.com"), response=httpx.Response(422))

    async def generate_cover_letter(self, user_id, app_id, language):
        self.languages.append(language)
        await asyncio.sleep(0.02)
        if self.fail == "timeout":
            raise httpx.ReadTimeout("timeout")
        if self.fail == "provider":
            raise httpx.HTTPStatusError("provider", request=httpx.Request("POST", "https://example.com"), response=httpx.Response(502))
        return {"letter": "Использую Python. " + str(len(self.languages))}


async def open_picker(state, message, api):
    message.bot = TypingBot()
    await state.update_data({APPLICATIONS_MESSAGE_ID: message.message_id, APPLICATIONS_APPLICATION_ID: 7, APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW})
    await handle_applications_callback(Callback("applications:letter:7:0", message), state, api)
    assert not api.languages
    assert "На каком языке" in message.text


async def click(state, message, api, action):
    token = (await state.get_data())["letter_token"]
    data = f"applications:letter_action:{token}:{action}"
    await handle_applications_callback(Callback(data, message), state, api)
    return data


async def wait_for_view(state, view):
    for _ in range(100):
        if (await state.get_data()).get(APPLICATIONS_VIEW) == view:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Timed out waiting for view={view}")


@pytest.mark.parametrize("language", ["en", "ru", "de", "fr", "es"])
def test_picker_generation_regeneration_change_and_back(language):
    async def scenario():
        storage, state = _state()
        message, api = Message(), LetterApi()
        await open_picker(state, message, api)
        assert len(message.reply_markup.inline_keyboard) == 7
        selected = await click(state, message, api, language)
        await wait_for_view(state, "letter")
        assert api.languages == [language]
        assert message.bot.actions == ["typing"]
        assert (await state.get_data())[APPLICATIONS_VIEW] == "letter"
        await handle_applications_callback(Callback(selected, message), state, api)
        retry = await click(state, message, api, "again")
        await handle_applications_callback(Callback(retry, message), state, api)
        await wait_for_view(state, "letter")
        assert api.languages == [language, language]
        await click(state, message, api, "change")
        assert (await state.get_data())[APPLICATIONS_VIEW] == "letter_language"
        await click(state, message, api, "fr")
        await wait_for_view(state, "letter")
        assert api.languages[-1] == "fr"
        await click(state, message, api, "back")
        assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_DETAIL_VIEW
        assert await state.get_state() is None
        await storage.close()
    asyncio.run(scenario())


def test_custom_invalid_retry_then_valid():
    async def scenario():
        storage, state = _state()
        message, api = Message(), LetterApi()
        await open_picker(state, message, api)
        await click(state, message, api, "custom")
        assert await state.get_state() == ApplicationsStates.waiting_for_letter_language.state
        for invalid in ("invent a project", None, "x" * 65):
            message.text = invalid
            await handle_letter_language_text(message, state, api)
            assert "Введите название ещё раз" in message.text
            assert not api.languages
        message.text = "Italiano"
        await handle_letter_language_text(message, state, api)
        await wait_for_view(state, "letter")
        assert api.languages == ["it"]
        assert await state.get_state() is None
        await click(state, message, api, "again")
        await wait_for_view(state, "letter")
        assert api.languages == ["it", "it"]
        await storage.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["timeout", "provider"])
def test_error_stops_typing_and_keeps_retry_change_back(failure):
    async def scenario():
        storage, state = _state()
        message, api = Message(), LetterApi()
        await open_picker(state, message, api)
        api.fail = failure
        await click(state, message, api, "de")
        await wait_for_view(state, "letter_error")
        assert (await state.get_data())[APPLICATIONS_VIEW] == "letter_error"
        assert "Не удалось" in message.text
        assert len(message.reply_markup.inline_keyboard) == 3
        assert not any("ChatActionSender._worker" in str(task.get_coro()) for task in asyncio.all_tasks())
        api.fail = None
        await click(state, message, api, "again")
        await wait_for_view(state, "letter")
        assert api.languages == ["de", "de"]
        await storage.close()
    asyncio.run(scenario())


def test_typing_repeats_and_context_change_cancels_request(monkeypatch):
    original = ChatActionSender.typing
    monkeypatch.setattr(ChatActionSender, "typing", lambda **kwargs: original(**{**kwargs, "interval": 0.02}))
    async def scenario():
        storage, state = _state()
        started, cancelled = asyncio.Event(), asyncio.Event()
        class Delayed(LetterApi):
            async def generate_cover_letter(self, user_id, app_id, language):
                self.languages.append(language)
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
        message, api = Message(), Delayed()
        await open_picker(state, message, api)
        await click(state, message, api, "en")
        await started.wait()
        await click(state, message, api, "again")
        await asyncio.sleep(0.06)
        assert len(message.bot.actions) >= 2
        assert api.languages == ["en"]
        await state.clear()
        await asyncio.wait_for(cancelled.wait(), 1)
        assert cancelled.is_set()
        count = len(message.bot.actions)
        await asyncio.sleep(0.04)
        assert len(message.bot.actions) == count
        assert await state.get_data() == {}
        await storage.close()
    asyncio.run(scenario())


def test_cancel_loading_returns_detail_and_ignores_late_result():
    async def scenario():
        storage, state = _state()
        started = asyncio.Event()
        class Delayed(LetterApi):
            async def generate_cover_letter(self, *args):
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    return {"letter": "Late result"}
        message, api = Message(), Delayed()
        await open_picker(state, message, api)
        await click(state, message, api, "en")
        await started.wait()
        await click(state, message, api, "back")
        await asyncio.sleep(0.3)
        assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_DETAIL_VIEW
        assert "Late result" not in message.text
        await storage.close()
    asyncio.run(scenario())


def test_dispatcher_picker_custom_input_generation(monkeypatch):
    async def scenario():
        bot, api = Bot("123456:applications-test-token"), LetterApi()
        edits, actions = [], []
        async def edit(message, text, **kwargs):
            edits.append((text, kwargs))
        async def bot_edit(bot, **kwargs):
            edits.append((kwargs["text"], kwargs))
        async def answer(callback, **kwargs):
            return True
        async def typing(bot, **kwargs):
            actions.append(kwargs["action"])
        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(TelegramMessage, "edit_text", edit)
        monkeypatch.setattr(Bot, "edit_message_text", bot_edit)
        monkeypatch.setattr(Bot, "send_chat_action", typing)
        monkeypatch.setattr(CallbackQuery, "answer", answer)
        state = await _dispatcher_state(bot, {APPLICATIONS_MESSAGE_ID: 10, APPLICATIONS_APPLICATION_ID: 7, APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW})
        await main_module.dp.feed_update(bot, _applications_callback_update("applications:letter:7:0", update_id=99, message_id=10))
        assert not api.languages
        custom = edits[-1][1]["reply_markup"].inline_keyboard[5][0].callback_data
        await main_module.dp.feed_update(bot, _applications_callback_update(custom, update_id=100, message_id=10))
        update = Update(update_id=101, message=TelegramMessage(message_id=11, date=datetime.now(), chat=Chat(id=state.key.chat_id, type="private"), from_user=User(id=state.key.user_id, is_bot=False, first_name="Test"), text="Italiano"))
        await main_module.dp.feed_update(bot, update)
        await wait_for_view(state, "letter")
        assert api.languages == ["it"]
        assert edits[-1][1]["parse_mode"] is None
        assert edits[-1][1]["message_id"] == 10
        assert actions == ["typing"]
        await bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_back_cancels_delayed_generation_without_waiting_for_request(monkeypatch):
    async def scenario():
        bot = Bot("123456:applications-test-token")
        started, cancelled, edits = asyncio.Event(), asyncio.Event(), []

        class DelayedApi(LetterApi):
            async def generate_cover_letter(self, user_id, app_id, language):
                self.languages.append(language)
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        api = DelayedApi()

        async def edit(message, text, **kwargs):
            edits.append((text, kwargs))

        async def answer(callback, **kwargs):
            return True

        async def typing(bot, **kwargs):
            return True

        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(TelegramMessage, "edit_text", edit)
        monkeypatch.setattr(CallbackQuery, "answer", answer)
        monkeypatch.setattr(Bot, "send_chat_action", typing)
        state = await _dispatcher_state(bot, {
            APPLICATIONS_MESSAGE_ID: 10,
            APPLICATIONS_APPLICATION_ID: 7,
            APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW,
        })
        await main_module.dp.feed_update(bot, _applications_callback_update(
            "applications:letter:7:0", update_id=201, message_id=10
        ))
        token = (await state.get_data())["letter_token"]
        await main_module.dp.feed_update(bot, _applications_callback_update(
            f"applications:letter_action:{token}:en", update_id=202, message_id=10
        ))
        await asyncio.wait_for(started.wait(), 1)
        loading_token = (await state.get_data())["letter_token"]
        await asyncio.wait_for(main_module.dp.feed_update(bot, _applications_callback_update(
            f"applications:letter_action:{loading_token}:back", update_id=203, message_id=10
        )), 0.2)
        await wait_for_view(state, APPLICATIONS_DETAIL_VIEW)
        await asyncio.wait_for(cancelled.wait(), 1)
        assert not any("Late result" in text for text, _ in edits)
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_change_before_user_resolution_never_starts_generation(monkeypatch):
    async def scenario():
        bot = Bot("123456:applications-test-token")
        user_resolution_started, release_user_resolution = asyncio.Event(), asyncio.Event()
        user_resolution_finished, generated = asyncio.Event(), asyncio.Event()
        edits, unhandled = [], []

        class DelayedUserApi(LetterApi):
            async def create_or_get_user(self, user):
                user_resolution_started.set()
                await release_user_resolution.wait()
                user_resolution_finished.set()
                return 1

            async def generate_cover_letter(self, user_id, app_id, language):
                generated.set()
                return {"letter": "Late result"}

        api = DelayedUserApi()

        async def edit(message, text, **kwargs):
            edits.append((text, kwargs))

        async def answer(callback, **kwargs):
            return True

        loop = asyncio.get_running_loop()
        previous_exception_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
        try:
            monkeypatch.setattr(main_module, "api_client", api)
            monkeypatch.setattr(TelegramMessage, "edit_text", edit)
            monkeypatch.setattr(CallbackQuery, "answer", answer)
            state = await _dispatcher_state(bot, {
                APPLICATIONS_MESSAGE_ID: 10,
                APPLICATIONS_APPLICATION_ID: 7,
                APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW,
            })
            await main_module.dp.feed_update(bot, _applications_callback_update(
                "applications:letter:7:0", update_id=211, message_id=10
            ))
            token = (await state.get_data())["letter_token"]
            await main_module.dp.feed_update(bot, _applications_callback_update(
                f"applications:letter_action:{token}:en", update_id=212, message_id=10
            ))
            await asyncio.wait_for(user_resolution_started.wait(), 1)
            loading_token = (await state.get_data())["letter_token"]
            await asyncio.wait_for(main_module.dp.feed_update(bot, _applications_callback_update(
                f"applications:letter_action:{loading_token}:change", update_id=213, message_id=10
            )), 0.2)
            current = await state.get_data()
            assert current[APPLICATIONS_VIEW] == "letter_language"
            assert current["letter_token"] != loading_token
            release_user_resolution.set()
            await asyncio.wait_for(user_resolution_finished.wait(), 1)
            await asyncio.sleep(0)
            assert not generated.is_set()
            assert not any("Late result" in text for text, _ in edits)
            assert not unhandled
        finally:
            loop.set_exception_handler(previous_exception_handler)
            await bot.session.close()

    asyncio.run(scenario())


def test_client_sends_only_language_and_validates_responses():
    async def scenario():
        api = JobHunterApiClient("https://example.com")
        def transport(request):
            assert request.method == "POST"
            if request.url.path == "/cover-letter/language":
                assert json.loads(request.content) == {"text": "Italiano"}
                return httpx.Response(200, json={"language": "it"})
            assert request.url.path == "/users/1/applications/7/cover-letter"
            assert json.loads(request.content) == {"language": "it"}
            return httpx.Response(200, json={"letter": None})
        await api._client.aclose()
        api._client = httpx.AsyncClient(base_url="https://example.com", transport=httpx.MockTransport(transport))
        assert await api.normalize_cover_letter_language("Italiano") == "it"
        with pytest.raises(httpx.DecodingError):
            await api.generate_cover_letter(1, 7, "it")
        await api.close()
    asyncio.run(scenario())
