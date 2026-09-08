import asyncio
import os
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage
from aiogram.types import Chat, Message, MessageEntity, Update, User

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:applications-test-token")
import app.main as main_module
from app.applications import (
    ApplicationsStates, APPLICATIONS_MESSAGE_ID, APPLICATIONS_VIEW,
    APPLICATIONS_OFFSET, APPLICATIONS_FILTER_STATUS, APPLICATION_NOT_FOUND_MESSAGE,
    APPLICATIONS_SORT,
)
from test_application_filters import setup


async def note_setup(monkeypatch, initial=None):
    ui, api, state = await setup(monkeypatch, count=6)
    api.notes, api.note_puts, api.note_deletes = {6: initial}, [], []
    api.error = None
    ui.deleted = []
    ui.fail_delete = False
    original_get = api.get_application
    async def get(user_id, app_id):
        if api.error == "get":
            raise httpx.ReadTimeout("test")
        result = await original_get(user_id, app_id)
        result["application"]["note"] = api.notes.get(app_id)
        return result
    async def put(user_id, app_id, note):
        api.note_puts.append((user_id, app_id, note))
        if api.error == "404":
            response = httpx.Response(404, request=httpx.Request("PUT", "http://api/note"))
            response.raise_for_status()
        if api.error in ("timeout", "committed_timeout"):
            if api.error == "committed_timeout":
                api.notes[app_id] = note
            raise httpx.ReadTimeout("test")
        api.notes[app_id] = note
        return await get(user_id, app_id)
    async def delete(user_id, app_id):
        api.note_deletes.append((user_id, app_id))
        if api.error in ("delete_timeout", "delete_committed_timeout"):
            if api.error == "delete_committed_timeout":
                api.notes[app_id] = None
            raise httpx.ReadTimeout("test")
        api.notes[app_id] = None
        return await get(user_id, app_id)
    async def edit_bot(_bot, **kwargs):
        return await ui.edit(SimpleNamespace(message_id=kwargs["message_id"]), kwargs["text"], **{
            key: value for key, value in kwargs.items() if key not in ("message_id", "chat_id", "text")
        })
    async def delete_bot(_bot, **kwargs):
        ui.deleted.append(kwargs["message_id"])
        if ui.fail_delete:
            raise TelegramBadRequest(
                method=DeleteMessage(chat_id=kwargs["chat_id"], message_id=kwargs["message_id"]),
                message="delete failed",
            )
    api.get_application, api.put_application_note, api.delete_application_note = get, put, delete
    monkeypatch.setattr(Bot, "edit_message_text", edit_bot)
    monkeypatch.setattr(Bot, "delete_message", delete_bot)
    for item in api.items:
        item["status"] = "interview"
    await ui.menu()
    await ui.click("Фильтр: Все")
    await ui.click("Собеседование")
    await ui.click("Вперёд ➡️")
    await ui.click("Vacancy 6")
    for item in api.items:
        item["next_action_due_on"] = f"2026-10-{int(item['app_id']):02d}"
    await state.update_data({APPLICATIONS_SORT: "next_action"})
    return ui, api, state


async def text_update(ui, text=None, **kwargs):
    ui.counter += 1
    if text == "/cancel":
        kwargs["entities"] = [MessageEntity(type="bot_command", offset=0, length=7)]
    await main_module.dp.feed_update(ui.bot, Update(update_id=ui.counter, message=Message(
        message_id=2000 + ui.counter, date=datetime.now(), chat=Chat(id=456, type="private"),
        from_user=User(id=123, is_bot=False, first_name="Anna"), text=text, **kwargs,
    )))


def labels(ui):
    return [b.text for row in ui.markup.inline_keyboard for b in row]


def test_dispatcher_note_lifecycle_and_duplicate_text(monkeypatch):
    async def scenario():
        ui, api, state = await note_setup(monkeypatch)
        try:
            assert "🗑 Удалить заметку" not in labels(ui)
            assert "📝 Заметка:" not in ui.text
            old_controls = [b.callback_data for row in ui.markup.inline_keyboard for b in row]
            await ui.click("📝 Заметка")
            assert "полностью заменена" not in ui.text
            assert await state.get_state() == ApplicationsStates.waiting_for_note.state
            before = (len(api.create_users), len(api.detail_calls), len(api.queries))
            for control in [*old_controls, "applications:detail:6:5", "applications:list:old:open:6", "applications:set:old:offer"]:
                await ui.feed(control)
            assert (len(api.create_users), len(api.detail_calls), len(api.queries)) == before
            note = "HR < > _ * https://example.com\nСозвон в четверг"
            await asyncio.gather(text_update(ui, " " + note + " "), text_update(ui, note))
            assert api.note_puts == [(4, 6, note)]
            assert "📝 Заметка:\n" + note in ui.text
            assert ui.parse_mode is None
            assert await state.get_state() is None
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == 101
            assert ui.deleted == [100]
            assert ui.sends == [100, 101]
            before = len(api.create_users)
            for control in old_controls:
                await ui.feed(control, message_id=100)
            assert len(api.create_users) == before
            await ui.click("📝 Заметка")
            assert f"📝 Текущая заметка:\n\n{note}" in ui.text
            assert "⚠️ Старая заметка будет полностью заменена." in ui.text
            await text_update(ui, "Replacement")
            assert api.note_puts[-1] == (4, 6, "Replacement")
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == 102
            assert ui.deleted[-1] == 101
            await ui.click("⬅️ К списку")
            assert api.queries[-1] == (4, "interview", 5, 5)
            assert api.sort_queries[-1] == "next_action"
            assert (await state.get_data())[APPLICATIONS_SORT] == "next_action"
            await ui.click("Vacancy 6")
            assert "Replacement" in ui.text
            await ui.click("🗑 Удалить заметку")
            assert api.note_deletes == [(4, 6)]
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == 103
            assert ui.deleted[-1] == 102
            assert "🗑 Удалить заметку" not in labels(ui)
            assert "📝 Заметка:" not in ui.text
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("command", [False, True])
def test_dispatcher_note_cancel_fresh_detail_and_context(monkeypatch, command):
    async def scenario():
        ui, api, state = await note_setup(monkeypatch, "Old")
        try:
            await ui.click("📝 Заметка")
            api.notes[6] = "Changed externally"
            if command:
                await text_update(ui, "/cancel")
            else:
                await ui.click("Отмена")
            assert "Changed externally" in ui.text
            assert api.note_puts == []
            assert await state.get_state() is None
            data = await state.get_data()
            assert data[APPLICATIONS_OFFSET] == 5
            assert data[APPLICATIONS_FILTER_STATUS] == "interview"
            assert data[APPLICATIONS_MESSAGE_ID] == 100
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("content", [
    {"photo": [{"file_id": "p", "file_unique_id": "p", "width": 1, "height": 1}]},
    {"document": {"file_id": "d", "file_unique_id": "d"}},
    {"sticker": {"file_id": "s", "file_unique_id": "s", "type": "regular", "width": 1, "height": 1, "is_animated": False, "is_video": False}},
    {"text": " "}, {"text": "x" * 1001},
])
def test_dispatcher_invalid_note_keeps_input_without_api(monkeypatch, content):
    async def scenario():
        ui, api, state = await note_setup(monkeypatch)
        try:
            await ui.click("📝 Заметка")
            calls = len(api.create_users)
            await text_update(ui, **content)
            assert api.note_puts == []
            assert len(api.create_users) == calls
            assert await state.get_state() == ApplicationsStates.waiting_for_note.state
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == 100
            if "text" not in content:
                assert ui.text == "Отправь текст заметки или нажми Отмена."
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("error", ["404", "timeout", "committed_timeout"])
def test_dispatcher_note_api_errors(monkeypatch, error):
    async def scenario():
        ui, api, state = await note_setup(monkeypatch)
        try:
            await ui.click("📝 Заметка")
            api.error = error
            await text_update(ui, "New")
            if error == "404":
                assert ui.text == APPLICATION_NOT_FOUND_MESSAGE
                assert await state.get_state() is None
                await ui.click("⬅️ К списку")
                assert api.queries[-1] == (4, "interview", 5, 5)
                assert api.sort_queries[-1] == "next_action"
            else:
                assert "Не удалось подтвердить" in ui.text
                assert await state.get_state() == ApplicationsStates.waiting_for_note.state
                api.error = None
                await text_update(ui, "New")
                assert api.notes[6] == "New"
                assert "📝 Заметка:\nNew" in ui.text
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_note_telegram_fallback_and_length(monkeypatch):
    async def scenario():
        ui, api, state = await note_setup(monkeypatch)
        try:
            await ui.click("📝 Заметка")
            api.items[-1]["title"] = "😀" * 4000
            ui.fail_delete, ui.fail_cleanup = True, True
            note = "😀" * 1000
            await text_update(ui, note)
            assert api.notes[6] == note
            assert note in ui.text
            assert len(ui.text.encode("utf-16-le")) // 2 <= 4096
            assert ui.deleted == [100]
            assert ui.cleaned == [100]
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == 101
            assert ui.parse_mode is None
            before = len(api.create_users)
            await ui.feed("applications:note:6:5", message_id=100)
            assert len(api.create_users) == before
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_note_saved_when_new_canonical_send_fails(monkeypatch):
    async def scenario():
        ui, api, state = await note_setup(monkeypatch)
        try:
            await ui.click("📝 Заметка")
            cancel = ui.button("Отмена")
            async def fail_send(message, text, **kwargs):
                from aiogram.exceptions import TelegramBadRequest
                from aiogram.methods import SendMessage
                raise TelegramBadRequest(method=SendMessage(chat_id=456, text=text), message="send failed")
            monkeypatch.setattr(Message, "answer", fail_send)
            await text_update(ui, "Persisted")
            assert api.notes[6] == "Persisted"
            assert api.note_puts == [(4, 6, "Persisted")]
            assert ui.deleted == [100]
            assert await state.get_state() is None
            assert (await state.get_data())[APPLICATIONS_VIEW] == "note_render_failed"
            await ui.feed(cancel, message_id=100)
            await text_update(ui, "Duplicate")
            assert len(api.note_puts) == 1
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] is None
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_note_cancel_load_failure_keeps_context(monkeypatch):
    async def scenario():
        ui, api, state = await note_setup(monkeypatch, "Old")
        try:
            await ui.click("📝 Заметка")
            old_cancel = ui.button("Отмена")
            api.error = "get"
            await ui.feed(old_cancel)
            assert await state.get_state() == ApplicationsStates.waiting_for_note.state
            assert "Не удалось подтвердить" in ui.text
            before = len(api.create_users)
            await ui.feed(old_cancel)
            assert len(api.create_users) == before
            api.error = None
            await ui.click("Отмена")
            assert "📝 Заметка:\nOld" in ui.text
            assert api.note_puts == []
            assert (await state.get_data())[APPLICATIONS_OFFSET] == 5
            assert (await state.get_data())[APPLICATIONS_FILTER_STATUS] == "interview"
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_initial_note_get_failure_does_not_enable_put(monkeypatch):
    async def scenario():
        ui, api, state = await note_setup(monkeypatch, "Existing note")
        try:
            note_callback = ui.button("📝 Заметка")
            api.error = "get"
            await ui.feed(note_callback, message_id=100)
            assert await state.get_state() is None
            data = await state.get_data()
            assert data[APPLICATIONS_VIEW] == "detail"
            assert data[APPLICATIONS_MESSAGE_ID] == 100
            assert data[APPLICATIONS_OFFSET] == 5
            assert data[APPLICATIONS_FILTER_STATUS] == "interview"
            assert ui.text == "Не удалось загрузить заметку. Попробуй ещё раз."
            await text_update(ui, "Must not overwrite")
            assert api.note_puts == []
            assert api.notes[6] == "Existing note"

            api.error = None
            await ui.feed(note_callback, message_id=100)
            assert await state.get_state() == ApplicationsStates.waiting_for_note.state
            assert "📝 Текущая заметка:\n\nExisting note" in ui.text
            assert "⚠️ Старая заметка будет полностью заменена." in ui.text
            assert api.note_puts == []
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("error", ["delete_timeout", "delete_committed_timeout"])
def test_dispatcher_delete_failure_does_not_enable_put_and_can_retry(monkeypatch, error):
    async def scenario():
        ui, api, state = await note_setup(monkeypatch, "Existing note")
        try:
            delete_callback = ui.button("🗑 Удалить заметку")
            api.error = error
            await ui.feed(delete_callback, message_id=100)
            assert await state.get_state() is None
            data = await state.get_data()
            assert data[APPLICATIONS_VIEW] == "detail"
            assert data[APPLICATIONS_MESSAGE_ID] == 100
            assert data[APPLICATIONS_OFFSET] == 5
            assert data[APPLICATIONS_FILTER_STATUS] == "interview"
            assert ui.text.startswith("Не удалось подтвердить удаление заметки.")
            await text_update(ui, "Must not recreate note")
            assert api.note_puts == []
            assert api.note_deletes == [(4, 6)]

            api.error = None
            await ui.feed(delete_callback, message_id=100)
            assert api.note_deletes == [(4, 6), (4, 6)]
            assert api.notes[6] is None
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == 102
            assert (await state.get_data())[APPLICATIONS_OFFSET] == 5
            assert (await state.get_data())[APPLICATIONS_FILTER_STATUS] == "interview"
            assert "📝 Заметка:" not in ui.text
            assert "🗑 Удалить заметку" not in labels(ui)
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("menu", ["💼 Добавить вакансию", "📋 Мои вакансии", "👤 Мой профиль"])
def test_dispatcher_menu_ends_note_input(monkeypatch, menu):
    async def scenario():
        ui, api, state = await note_setup(monkeypatch)
        async def profile(*args):
            return None
        api.get_user_profile = profile
        try:
            await ui.click("📝 Заметка")
            cancel = ui.button("Отмена")
            ui.fail_cleanup = True
            await ui.menu(menu)
            assert await state.get_state() != ApplicationsStates.waiting_for_note.state
            assert 100 in ui.cleaned
            before = len(api.create_users)
            await ui.feed(cancel, message_id=100)
            assert len(api.create_users) == before
            assert api.note_puts == []
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())
