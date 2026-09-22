import asyncio
import os

import httpx
import pytest
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Message

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:follow-ups-test-token")

import app.main as main_module
from app.applications import (
    APPLICATIONS_APPLICATION_ID,
    APPLICATIONS_DETAIL_VIEW,
    APPLICATIONS_FOLLOW_UPS_OFFSET,
    APPLICATIONS_FOLLOW_UPS_TOKEN,
    APPLICATIONS_FOLLOW_UPS_VIEW,
    APPLICATIONS_LIST_TOKEN,
    APPLICATIONS_LIST_VIEW,
    APPLICATIONS_MESSAGE_ID,
    APPLICATIONS_OFFSET,
    APPLICATIONS_VIEW,
    _follow_up_queue_content,
    _utf16_units,
)
from test_applications import DispatcherApi, _applications_callback_update, _dispatcher_state


class FollowUpsApi(DispatcherApi):
    def __init__(self) -> None:
        super().__init__()
        self.follow_up_calls: list[tuple[int, int, int]] = []
        self.follow_up_pages: dict[int, dict[str, object]] = {}

    async def list_application_follow_ups(
        self, user_id: int, *, limit: int, offset: int,
    ) -> dict[str, object]:
        self.follow_up_calls.append((user_id, limit, offset))
        return self.follow_up_pages[offset]


def _item(application_id: int, due_state: str, due_on: str, status: str = "applied") -> dict[str, object]:
    return {
        "application_id": application_id,
        "title": f"Title {application_id}",
        "company": f"Company {application_id}",
        "status": status,
        "next_action": f"Action {application_id}",
        "next_action_due_on": due_on,
        "due_state": due_state,
    }


def test_follow_up_queue_uses_compact_actions_and_safe_vacancy_context() -> None:
    long_title = "Senior \U0001f9e0 Python Backend Engineer " * 12
    text, _ = _follow_up_queue_content([
        {
            **_item(1, "overdue", "2026-09-09", "saved"),
            "title": f"{long_title} <Python>",
            "company": None,
            "next_action": "Написать <HR> & follow up",
        },
        {
            **_item(2, "today", "2026-09-22", "withdrawn"),
            "company": "ACME <Labs>",
        },
        _item(3, "upcoming", "2026-09-25", "applied"),
    ], offset=0, has_next=False, token="token")

    assert "<b>⚠️ Срок прошёл</b>" in text
    assert "<b>📍 На сегодня</b>" in text
    assert "<b>📅 Запланировано</b>" in text
    assert "⚠️ Просрочено" not in text
    assert "Сегодня (UTC)" not in text
    assert "\nДалее\n" not in text
    assert "09.09 · Написать &lt;HR&gt; &amp; follow up" in text
    assert "«Написать HR»" not in text
    assert "22.09 · Action 2" in text
    assert text.count("Сегодня") == 1
    assert text.count("UTC") == 1
    assert "«Сегодня» определяется по UTC." in text
    assert "Статус: Сохранена" in text
    assert "Статус: Я прекратил процесс" in text
    assert "Компания не указана" not in text
    assert "…" in text
    assert "<Python>" not in text
    assert "<Labs>" not in text
    assert _utf16_units(text) <= 4096


def test_dispatcher_follow_up_queue_utc_navigation_terminal_statuses_and_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:follow-ups-test-token")
        api = FollowUpsApi()
        api.detail = {
            "application": {"id": 18, "status": "rejected", "note": None, "next_action": "Action 18", "next_action_due_on": "2026-09-27"},
            "job": {"title": "Title 18", "workplace_type": "remote"},
        }
        api.follow_up_pages = {
            0: {"items": [
                _item(18, "overdue", "2026-09-27", "rejected"),
                _item(19, "today", "2026-09-28", "hired"),
                _item(20, "upcoming", "2026-09-29", "withdrawn"),
            ], "has_next": True},
            5: {"items": [_item(21, "upcoming", "2026-10-01")], "has_next": False},
        }
        edits: list[tuple[str, object | None]] = []
        parse_modes: list[object | None] = []

        async def edit(message: Message, text: str, **kwargs: object) -> None:
            edits.append((text, kwargs.get("reply_markup")))
            parse_modes.append(kwargs.get("parse_mode"))

        async def ack(callback: CallbackQuery, **kwargs: object) -> bool:
            return True

        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "edit_text", edit)
        monkeypatch.setattr(CallbackQuery, "answer", ack)
        state = await _dispatcher_state(bot, {
            APPLICATIONS_MESSAGE_ID: 10,
            APPLICATIONS_VIEW: APPLICATIONS_LIST_VIEW,
            APPLICATIONS_OFFSET: 0,
            APPLICATIONS_LIST_TOKEN: "list-token",
        })
        try:
            await main_module.dp.feed_update(
                bot, _applications_callback_update("applications:list:list-token:follow_ups", update_id=1, message_id=10),
            )
            text, markup = edits[-1]
            assert "<b>⚠️ Срок прошёл</b>" in text
            assert "<b>📍 На сегодня</b>" in text
            assert "<b>📅 Запланировано</b>" in text
            assert "⚠️ Просрочено" not in text
            assert "Сегодня (UTC)" not in text
            assert "\nДалее\n" not in text
            assert "27.09 · Action 18" in text
            assert "28.09 · Action 19" in text
            assert "«Action 18»" not in text
            assert text.count("Сегодня") == 1
            assert text.count("UTC") == 1
            assert "Статус: Отказ" in text
            assert "Статус: Вышел на работу" in text
            assert "Статус: Я прекратил процесс" in text
            assert parse_modes[-1] == ParseMode.HTML
            assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_FOLLOW_UPS_VIEW
            token = (await state.get_data())[APPLICATIONS_FOLLOW_UPS_TOKEN]
            assert api.follow_up_calls == [(4, 5, 0)]
            buttons = [button for row in markup.inline_keyboard for button in row]
            open_callback = next(button.callback_data for button in buttons if button.text == "Title 18")
            page_callback = next(button.callback_data for button in buttons if button.text == "Вперёд ➡️")

            await main_module.dp.feed_update(
                bot, _applications_callback_update(open_callback, update_id=2, message_id=10),
            )
            assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_DETAIL_VIEW
            back = edits[-1][1].inline_keyboard[-1][0]
            assert back.text == "⬅️ К действиям"
            assert back.callback_data == "applications:followups:return:0"

            await main_module.dp.feed_update(
                bot, _applications_callback_update(back.callback_data, update_id=3, message_id=10),
            )
            assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_FOLLOW_UPS_VIEW
            refreshed_token = (await state.get_data())[APPLICATIONS_FOLLOW_UPS_TOKEN]
            assert refreshed_token != token

            await main_module.dp.feed_update(
                bot, _applications_callback_update(page_callback, update_id=4, message_id=10),
            )
            assert api.follow_up_calls == [(4, 5, 0), (4, 5, 0)]
            assert (await state.get_data())[APPLICATIONS_FOLLOW_UPS_OFFSET] == 0
            current_markup = edits[-1][1]
            current_page_callback = next(
                button.callback_data for row in current_markup.inline_keyboard for button in row
                if button.text == "Вперёд ➡️"
            )
            await main_module.dp.feed_update(
                bot, _applications_callback_update(current_page_callback, update_id=5, message_id=10),
            )
            assert api.follow_up_calls[-1] == (4, 5, 5)
            assert "01.10 · Action 21" in edits[-1][0]
        finally:
            await state.clear()
            await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_follow_up_empty_error_and_stale_callbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:follow-ups-test-token")
        api = FollowUpsApi()
        api.follow_up_pages = {0: {"items": [], "has_next": False}}
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
            APPLICATIONS_LIST_TOKEN: "list-token",
        })
        try:
            await main_module.dp.feed_update(
                bot, _applications_callback_update("applications:list:list-token:follow_ups", update_id=10, message_id=10),
            )
            assert edits[-1][0] == "🔔 Что требует внимания\n\nЗапланированных действий пока нет."
            token = (await state.get_data())[APPLICATIONS_FOLLOW_UPS_TOKEN]
            before = len(api.follow_up_calls)
            await main_module.dp.feed_update(
                bot, _applications_callback_update(f"applications:followups:{token}:page:5", update_id=11, message_id=10),
            )
            assert len(api.follow_up_calls) == before
            await main_module.dp.feed_update(
                bot, _applications_callback_update("applications:followups:stale:back:0", update_id=12, message_id=10),
            )
            assert len(api.follow_up_calls) == before

            async def fail(*args: object, **kwargs: object) -> dict[str, object]:
                raise httpx.ConnectError("test")

            api.list_application_follow_ups = fail
            await state.update_data({
                APPLICATIONS_VIEW: APPLICATIONS_LIST_VIEW,
                APPLICATIONS_LIST_TOKEN: "retry-token",
            })
            await main_module.dp.feed_update(
                bot, _applications_callback_update("applications:list:retry-token:follow_ups", update_id=13, message_id=10),
            )
            assert "Не удалось загрузить запланированные действия" in edits[-1][0]
        finally:
            await state.clear()
            await bot.session.close()

    asyncio.run(scenario())
