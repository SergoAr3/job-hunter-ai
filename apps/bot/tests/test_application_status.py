import asyncio
import os
from types import SimpleNamespace

import httpx
import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import CallbackQuery, Message, Update, User, Chat
from datetime import datetime

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:applications-test-token")
import app.main as main_module
from app.applications import (
    APPLICATIONS_MESSAGE_ID, APPLICATIONS_VIEW, APPLICATIONS_APPLICATION_ID,
    APPLICATIONS_OFFSET, APPLICATIONS_STATUS_TOKEN, APPLICATIONS_DETAIL_VIEW,
    APPLICATIONS_SORT, STATUS_LABELS, APPLICATION_NOT_FOUND_MESSAGE,
)
from test_applications import list_callback, DispatcherApi, _dispatcher_state, _applications_callback_update


@pytest.mark.parametrize("missing_on", ["open", "set", "back"])
def test_dispatcher_status_not_found_returns_to_list(monkeypatch, missing_on):
    async def scenario():
        bot = Bot("123456:applications-test-token")
        api = DispatcherApi()
        api.detail["application"] = {"id": 18, "status": "saved"}
        api.pages = [{"items": [], "has_next": False}]
        edits, puts = [], []

        async def missing(*args):
            response = httpx.Response(
                404, json={"detail": {"code": "APPLICATION_NOT_FOUND"}},
                request=httpx.Request("GET", "http://api/users/4/applications/18"),
            )
            response.raise_for_status()

        async def put(*args):
            puts.append(args)
            await missing()

        async def edit(message, text, **kwargs):
            edits.append((message.message_id, text, kwargs["reply_markup"]))

        async def ack(callback, **kwargs):
            return True

        api.put_application_status = put
        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "edit_text", edit)
        monkeypatch.setattr(CallbackQuery, "answer", ack)
        state = await _dispatcher_state(bot, {
            APPLICATIONS_MESSAGE_ID: 10, APPLICATIONS_VIEW: "list", APPLICATIONS_OFFSET: 0,
        })
        counter = 0

        async def feed(data):
            nonlocal counter
            counter += 1
            await main_module.dp.feed_update(bot, _applications_callback_update(data, update_id=counter, message_id=10))

        try:
            await feed(await list_callback(state, "open:18"))
            if missing_on == "open":
                api.get_application = missing
                failed_callback = "applications:status:18:0"
            else:
                await feed("applications:status:18:0")
                token = (await state.get_data())[APPLICATIONS_STATUS_TOKEN]
                if missing_on == "back":
                    api.get_application = missing
                    failed_callback = f"applications:status_back:{token}"
                else:
                    failed_callback = f"applications:set:{token}:offer"
            await feed(failed_callback)
            data = await state.get_data()
            assert data[APPLICATIONS_STATUS_TOKEN] is None
            assert data[APPLICATIONS_APPLICATION_ID] is None
            assert data[APPLICATIONS_MESSAGE_ID] == 10
            assert edits[-1][1] == APPLICATION_NOT_FOUND_MESSAGE
            buttons = [b for row in edits[-1][2].inline_keyboard for b in row]
            assert [(b.text, b.callback_data) for b in buttons] == [("⬅️ К списку", "applications:page:0")]
            before = (len(edits), len(api.create_users))
            await feed(failed_callback)
            await feed("applications:status:18:0")
            await feed("applications:match:18:0")
            assert (len(edits), len(api.create_users)) == before
            assert api.match_calls == []
            assert puts == ([(4, 18, "offer")] if missing_on == "set" else [])
            await feed(buttons[0].callback_data)
            assert api.list_calls == [4]
            assert (await state.get_data())[APPLICATIONS_VIEW] == "list"
        finally:
            await state.clear()
            await bot.session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [None, "api", "telegram"])
def test_dispatcher_status_lifecycle(monkeypatch, failure):
    async def scenario():
        bot = Bot("123456:applications-test-token")
        api = DispatcherApi()
        api.detail["application"] = {"id": 18, "status": "saved"}
        api.pages = [{"items": [], "has_next": False}, {"items": [{"app_id": 18, "title": "Python", "status": "offer"}], "has_next": False}]
        puts, edits, sends, cleaned = [], [], [], []
        fail_edit = False

        async def put(user_id, app_id, status):
            puts.append((user_id, app_id, status))
            if failure == "api":
                raise httpx.ConnectError("test")
            api.detail["application"]["status"] = status
            return api.detail

        async def edit(message, text, **kwargs):
            if fail_edit:
                raise TelegramBadRequest(method=EditMessageText(chat_id=456, message_id=10, text=text), message="test")
            edits.append((message.message_id, text, kwargs.get("reply_markup")))

        async def answer(message, text, **kwargs):
            sends.append((text, kwargs.get("reply_markup")))
            return SimpleNamespace(message_id=20)

        async def cleanup(_bot, **kwargs):
            cleaned.append(kwargs["message_id"])
            raise TelegramBadRequest(method=EditMessageText(chat_id=456, message_id=10, text="x"), message="cleanup failed")

        async def ack(callback, **kwargs):
            return True

        api.put_application_status = put
        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "edit_text", edit)
        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", cleanup)
        monkeypatch.setattr(CallbackQuery, "answer", ack)
        state = await _dispatcher_state(bot, {
            APPLICATIONS_MESSAGE_ID: 10, APPLICATIONS_VIEW: "list", APPLICATIONS_OFFSET: 5,
            APPLICATIONS_SORT: "oldest",
        })
        counter = 0

        async def feed(data, message_id=10):
            nonlocal counter
            counter += 1
            await main_module.dp.feed_update(bot, _applications_callback_update(data, update_id=counter, message_id=message_id))

        await feed(await list_callback(state, "open:18"))
        assert "Статус: Сохранена" in edits[-1][1]
        await feed("applications:status:999:5")
        assert (await state.get_data()).get(APPLICATIONS_STATUS_TOKEN) is None
        await feed("applications:status:18:5")
        old_token = (await state.get_data())[APPLICATIONS_STATUS_TOKEN]
        labels = [b.text for row in edits[-1][2].inline_keyboard for b in row]
        assert labels == ["✓ Сохранена", "Откликнулся", "Собеседование", "Отказ", "Оффер", "⬅️ Назад"]
        await feed(f"applications:status_back:{old_token}")
        assert puts == []
        assert (await state.get_data())[APPLICATIONS_STATUS_TOKEN] is None
        await feed("applications:status:18:5")
        token = (await state.get_data())[APPLICATIONS_STATUS_TOKEN]
        assert token != old_token
        await feed(f"applications:set:{token}:garbage")
        assert (await state.get_data())[APPLICATIONS_STATUS_TOKEN] == token
        await feed(f"applications:set:{old_token}:offer")
        await feed("applications:match:18:5")  # old detail controls while picker active
        assert puts == api.match_calls == []
        fail_edit = failure == "telegram"
        await feed(f"applications:set:{token}:offer")
        assert puts == [(4, 18, "offer")]
        data = await state.get_data()
        assert data[APPLICATIONS_STATUS_TOKEN] is None
        assert data[APPLICATIONS_VIEW] == APPLICATIONS_DETAIL_VIEW
        assert data[APPLICATIONS_OFFSET] == 5
        assert data[APPLICATIONS_APPLICATION_ID] == 18
        assert data[APPLICATIONS_SORT] == "oldest"
        await feed(f"applications:set:{token}:rejected")
        assert len(puts) == 1
        if failure == "api":
            assert "Не удалось подтвердить" in edits[-1][1]
            assert api.detail["application"]["status"] == "saved"
        elif failure == "telegram":
            assert cleaned == [10]
            assert data[APPLICATIONS_MESSAGE_ID] == 20
            assert "Статус: Оффер" in sends[-1][0]
            await feed("applications:status:18:5")  # old message
            assert (await state.get_data())[APPLICATIONS_STATUS_TOKEN] is None
        else:
            assert sends == []
            assert data[APPLICATIONS_MESSAGE_ID] == 10
            assert "Статус: Оффер" in edits[-1][1]
            await feed("applications:match:18:5")
            await feed("applications:detail:18:5")
            assert "Статус: Оффер" in edits[-1][1]
            await feed("applications:status:18:5")
            nav_token = (await state.get_data())[APPLICATIONS_STATUS_TOKEN]
            await feed("applications:page:5")
            await feed(f"applications:set:{nav_token}:rejected")
            assert len(puts) == 1
            await feed(await list_callback(state, "open:18"))
            await feed("applications:status:18:5")
            menu_token = (await state.get_data())[APPLICATIONS_STATUS_TOKEN]
            await main_module.dp.feed_update(bot, Update(update_id=100, message=Message(
                message_id=30, date=datetime.now(), chat=Chat(id=456, type="private"),
                from_user=User(id=123, is_bot=False, first_name="Anna"), text="💼 Добавить вакансию",
            )))
            await feed(f"applications:set:{menu_token}:rejected")
            assert len(puts) == 1
            assert (await state.get_data()).get(APPLICATIONS_STATUS_TOKEN) is None
        assert set(api.create_users) == {123}
        await state.clear()
        await bot.session.close()

    asyncio.run(scenario())
