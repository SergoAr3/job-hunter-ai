import asyncio
import os

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Message

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:trustworthy-statuses-test-token")

import app.main as main_module
from app.applications import (
    APPLICATIONS_APPLICATION_ID,
    APPLICATIONS_DETAIL_VIEW,
    APPLICATIONS_LIST_VIEW,
    APPLICATIONS_MESSAGE_ID,
    APPLICATIONS_OFFSET,
    APPLICATIONS_STATUS_TOKEN,
    APPLICATIONS_VIEW,
    STATUS_LABELS,
)
from test_applications import DispatcherApi, _applications_callback_update, _dispatcher_state


@pytest.mark.parametrize(
    ("status", "label"),
    [
        ("recruiter_response", "HR ответил"),
        ("hired", "Вышел на работу"),
        ("withdrawn", "Я прекратил процесс"),
    ],
)
def test_dispatcher_selects_and_displays_each_explicit_status(
    monkeypatch: pytest.MonkeyPatch, status: str, label: str,
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:trustworthy-statuses-test-token")
        api = DispatcherApi()
        api.detail = {"application": {"id": 18, "status": "saved", "note": None}, "job": {"title": "Python", "workplace_type": "remote"}}
        edits: list[tuple[str, object | None]] = []

        async def put(user_id: int, app_id: int, value: str) -> dict[str, object]:
            assert (user_id, app_id, value) == (4, 18, status)
            api.detail["application"]["status"] = value
            return api.detail

        async def edit(message: Message, text: str, **kwargs: object) -> None:
            edits.append((text, kwargs.get("reply_markup")))

        async def ack(callback: CallbackQuery, **kwargs: object) -> bool:
            return True

        api.put_application_status = put
        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "edit_text", edit)
        monkeypatch.setattr(CallbackQuery, "answer", ack)
        state = await _dispatcher_state(bot, {
            APPLICATIONS_MESSAGE_ID: 10,
            APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW,
            APPLICATIONS_APPLICATION_ID: 18,
            APPLICATIONS_OFFSET: 0,
        })
        try:
            await main_module.dp.feed_update(
                bot, _applications_callback_update("applications:status:18:0", update_id=1, message_id=10),
            )
            token = (await state.get_data())[APPLICATIONS_STATUS_TOKEN]
            labels = [button.text for row in edits[-1][1].inline_keyboard for button in row]
            assert label in labels
            await main_module.dp.feed_update(
                bot, _applications_callback_update(f"applications:set:{token}:{status}", update_id=2, message_id=10),
            )
            assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_DETAIL_VIEW
            assert f"Статус: {label}" in edits[-1][0]
        finally:
            await state.clear()
            await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_status_filter_and_history_render_new_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:trustworthy-statuses-test-token")
        api = DispatcherApi()
        api.history = {"items": [
            {"status": "withdrawn", "occurred_at": "2026-09-21T12:00:00+00:00"},
            {"status": "hired", "occurred_at": "2026-09-20T12:00:00+00:00"},
            {"status": "recruiter_response", "occurred_at": "2026-09-19T12:00:00+00:00"},
        ]}
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
            APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW,
            APPLICATIONS_APPLICATION_ID: 18,
            APPLICATIONS_OFFSET: 0,
        })
        try:
            await main_module.dp.feed_update(
                bot, _applications_callback_update("applications:history:18:0", update_id=10, message_id=10),
            )
            for label in ("HR ответил", "Вышел на работу", "Я прекратил процесс"):
                assert label in edits[-1][0]
            assert edits[-1][0].index("HR ответил") < edits[-1][0].index("Вышел на работу")
            assert edits[-1][0].index("Вышел на работу") < edits[-1][0].index("Я прекратил процесс")
            await state.set_data({
                APPLICATIONS_MESSAGE_ID: 10,
                APPLICATIONS_VIEW: APPLICATIONS_LIST_VIEW,
                APPLICATIONS_OFFSET: 0,
                "applications_list_token": "list-token",
            })
            await main_module.dp.feed_update(
                bot, _applications_callback_update("applications:list:list-token:filter", update_id=11, message_id=10),
            )
            labels = [button.text for row in edits[-1][1].inline_keyboard for button in row]
            assert all(label in labels for label in ("HR ответил", "Вышел на работу", "Я прекратил процесс"))
        finally:
            await state.clear()
            await bot.session.close()

    asyncio.run(scenario())
