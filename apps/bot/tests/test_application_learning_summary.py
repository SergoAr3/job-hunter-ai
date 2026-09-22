import asyncio
import os

import httpx
import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Message

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:learning-summary-test-token")

import app.main as main_module
from app.applications import (
    APPLICATIONS_LIST_VIEW,
    APPLICATIONS_MESSAGE_ID,
    APPLICATIONS_OFFSET,
    APPLICATIONS_SUMMARY_VIEW,
    APPLICATIONS_VIEW,
)
from app.jobs import AddJobStates, REQUEST_URL_MESSAGE
from app.menu import ADD_JOB_BUTTON, main_menu_action
from test_applications import DispatcherApi, _applications_callback_update, _dispatcher_state


def test_dispatcher_summary_renders_null_denominator_incomplete_warning_and_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:learning-summary-test-token")
        api = DispatcherApi()
        api.pages = [{"items": [], "has_next": False}]
        api.learning_summary = {
            "total_applications": 3,
            "applied_count": 0,
            "interview_count": 0,
            "offer_count": 0,
            "applied_to_interview": {"numerator": 0, "denominator": 0, "percentage": None},
            "applied_to_offer": {"numerator": 0, "denominator": 0, "percentage": None},
            "history_missing_count": 1,
            "funnel_incomplete_count": 2,
            "as_of": "2026-09-21T00:00:00+00:00",
        }
        edits: list[tuple[str, object | None]] = []

        async def edit(message: Message, text: str, **kwargs: object) -> None:
            edits.append((text, kwargs.get("reply_markup")))

        async def ack(callback: CallbackQuery, **kwargs: object) -> bool:
            return True

        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "edit_text", edit)
        monkeypatch.setattr(CallbackQuery, "answer", ack)
        state = await _dispatcher_state(bot, {
            APPLICATIONS_MESSAGE_ID: 10,
            APPLICATIONS_VIEW: APPLICATIONS_LIST_VIEW,
            APPLICATIONS_OFFSET: 0,
        })

        await main_module.dp.feed_update(
            bot, _applications_callback_update("applications:list:initial-list:summary", update_id=1, message_id=10),
        )
        assert api.learning_summary_calls == [4]
        assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_SUMMARY_VIEW
        assert "Отклик → интервью: нет данных" in edits[-1][0]
        assert "Отклик → оффер: нет данных" in edits[-1][0]
        assert "⚠️ Неполная история: 3" in edits[-1][0]
        back = edits[-1][1].inline_keyboard[0][0]
        assert (back.text, back.callback_data) == ("⬅️ К списку", "applications:page:0")

        await main_module.dp.feed_update(
            bot, _applications_callback_update("applications:list:initial-list:summary", update_id=2, message_id=10),
        )
        assert api.learning_summary_calls == [4]
        await main_module.dp.feed_update(
            bot, _applications_callback_update(back.callback_data, update_id=3, message_id=10),
        )
        assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_LIST_VIEW
        assert api.list_calls == [4]
        await state.clear()
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_summary_api_error_keeps_a_back_navigation_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:learning-summary-test-token")
        api = DispatcherApi()
        api.pages = [{"items": [], "has_next": False}]
        edits: list[tuple[str, object | None]] = []

        async def fail_summary(user_id: int) -> dict[str, object]:
            raise httpx.ConnectError("test")

        async def edit(message: Message, text: str, **kwargs: object) -> None:
            edits.append((text, kwargs.get("reply_markup")))

        async def ack(callback: CallbackQuery, **kwargs: object) -> bool:
            return True

        api.get_application_learning_summary = fail_summary
        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "edit_text", edit)
        monkeypatch.setattr(CallbackQuery, "answer", ack)
        state = await _dispatcher_state(bot, {
            APPLICATIONS_MESSAGE_ID: 10,
            APPLICATIONS_VIEW: APPLICATIONS_LIST_VIEW,
            APPLICATIONS_OFFSET: 0,
        })

        await main_module.dp.feed_update(
            bot, _applications_callback_update("applications:list:initial-list:summary", update_id=10, message_id=10),
        )
        assert "Не удалось загрузить статистику" in edits[-1][0]
        assert (await state.get_data())[APPLICATIONS_VIEW] == "learning_summary_error"
        back = edits[-1][1].inline_keyboard[0][0]
        await main_module.dp.feed_update(
            bot, _applications_callback_update(back.callback_data, update_id=11, message_id=10),
        )
        assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_LIST_VIEW
        await state.clear()
        await bot.session.close()

    asyncio.run(scenario())


def test_menu_navigation_away_cleans_summary_keyboard() -> None:
    class CleanupBot:
        def __init__(self) -> None:
            self.cleaned: list[tuple[int, int]] = []

        async def edit_message_reply_markup(
            self, *, chat_id: int, message_id: int, reply_markup: object | None = None,
        ) -> None:
            assert reply_markup is None
            self.cleaned.append((chat_id, message_id))

    class MenuMessage:
        def __init__(self, bot: CleanupBot) -> None:
            self.text = ADD_JOB_BUTTON
            self.bot = bot
            self.chat = type("Chat", (), {"id": 456})()
            self.from_user = type("User", (), {"id": 123})()
            self.answers: list[str] = []

        async def answer(self, text: str, **kwargs: object) -> object:
            self.answers.append(text)
            return object()

    async def scenario() -> None:
        bot = CleanupBot()
        message = MenuMessage(bot)
        telegram_bot = Bot("123456:learning-summary-test-token")
        state = await _dispatcher_state(telegram_bot, {
            APPLICATIONS_MESSAGE_ID: 10,
            APPLICATIONS_VIEW: APPLICATIONS_SUMMARY_VIEW,
            APPLICATIONS_OFFSET: 0,
        })
        try:
            await main_menu_action(message, state)
            assert bot.cleaned == [(456, 10)]
            assert await state.get_state() == AddJobStates.waiting_for_url.state
            assert message.answers == [REQUEST_URL_MESSAGE]
        finally:
            await state.clear()
            await telegram_bot.session.close()

    asyncio.run(scenario())
