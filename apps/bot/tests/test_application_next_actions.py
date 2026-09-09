import asyncio

import httpx
import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import Message

from app.applications import (
    ApplicationsStates, APPLICATIONS_MESSAGE_ID, APPLICATIONS_VIEW,
    APPLICATIONS_OFFSET, APPLICATIONS_FILTER_STATUS, APPLICATIONS_NEXT_ACTION_DRAFT,
    APPLICATIONS_NEXT_ACTION_TOKEN,
    APPLICATIONS_SORT,
)
from test_application_notes import note_setup, text_update


async def setup(monkeypatch, initial=None):
    ui, api, state = await note_setup(monkeypatch, "Keep note")
    api.actions = initial
    api.items[-1]["next_action_due_on"] = initial[1] if initial else None
    api.action_puts, api.action_deletes = [], []
    api.action_error = None
    original = api.get_application
    async def get(user_id, app_id):
        result = await original(user_id, app_id)
        result["application"].update(next_action=api.actions[0] if api.actions else None,
                                     next_action_due_on=api.actions[1] if api.actions else None)
        return result
    async def write(user_id, app_id, action, due_on):
        api.action_puts.append((user_id, app_id, action, due_on))
        if api.action_error not in ("timeout", "500", "404"):
            api.actions = (action, due_on)
            api.items[-1]["next_action_due_on"] = due_on
        fail()
        return await get(user_id, app_id)
    def fail():
        if api.action_error in ("timeout", "committed_timeout"):
            raise httpx.ReadTimeout("test")
        if api.action_error in ("500", "404"):
            httpx.Response(int(api.action_error), request=httpx.Request("PUT", "http://api/test")).raise_for_status()
    async def delete(user_id, app_id):
        api.action_deletes.append((user_id, app_id))
        if api.action_error not in ("timeout", "500", "404"):
            api.actions = None
            api.items[-1]["next_action_due_on"] = None
        fail()
        return await get(user_id, app_id)
    api.get_application, api.set_application_next_action, api.delete_application_next_action = get, write, delete
    return ui, api, state


def run(monkeypatch, scenario, initial=None):
    async def wrapped():
        ui, api, state = await setup(monkeypatch, initial)
        try:
            await scenario(ui, api, state)
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(wrapped())


def test_create_replace_delete_canonical_and_stale(monkeypatch):
    async def scenario(ui, api, state):
        old_controls = [b.callback_data for row in ui.markup.inline_keyboard for b in row]
        await ui.click("📅 Следующее действие")
        old_cancel = ui.button("Отмена")
        assert await state.get_state() == ApplicationsStates.waiting_for_next_action.state
        await text_update(ui, " Написать HR ")
        assert await state.get_state() == ApplicationsStates.waiting_for_next_action_due_on.state
        for control in [old_cancel, *old_controls]:
            await ui.feed(control, message_id=100)
        assert await state.get_state() == ApplicationsStates.waiting_for_next_action_due_on.state
        await asyncio.gather(text_update(ui, "12.09.2026"), text_update(ui, "12.09.2026"))
        assert api.action_puts == [(4, 6, "Написать HR", "2026-09-12")]
        assert ui.deleted == [100, 101]
        assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == 102
        assert "12.09.2026 — Написать HR" in ui.text
        assert ui.text.index("Статус:") < ui.text.index("📅 Следующее действие:") < ui.text.index("📝 Заметка:")
        for control in old_controls:
            await ui.feed(control, message_id=100)
        await ui.click("📅 Следующее действие")
        assert "Текущее действие и дата будут полностью заменены" in ui.text
        await text_update(ui, "Replacement")
        await text_update(ui, "01.01.2000")
        assert api.actions == ("Replacement", "2000-01-01")
        await ui.click("🗑 Удалить следующее действие")
        assert api.actions is None
        assert "📅 Следующее действие:" not in ui.text
        assert await state.get_state() is None
        await ui.click("⬅️ К списку")
        assert api.queries[-1] == (4, "interview", 5, 5)
        assert api.sort_queries[-1] == "next_action"
        assert (await state.get_data())[APPLICATIONS_SORT] == "next_action"
        assert api.puts == []  # no status mutation, hence no history write
        assert api.note_puts == []
    run(monkeypatch, scenario)


@pytest.mark.parametrize("delete", [False, True])
def test_next_action_change_then_back_fetches_fresh_sorted_page(monkeypatch, delete):
    async def scenario(ui, api, state):
        for item in api.items:
            item["next_action_due_on"] = None
        if delete:
            await ui.click("📅 Следующее действие")
            await ui.click("Отмена")
            await ui.click("🗑 Удалить следующее действие")
        else:
            api.items[0]["next_action_due_on"] = "2026-09-10"
            await ui.click("📅 Следующее действие")
            await text_update(ui, "Follow up")
            await text_update(ui, "12.09.2026")
        before = len(api.queries)
        await ui.click("⬅️ К списку")
        assert len(api.queries) == before + 1
        assert api.sort_queries[-1] == "next_action"
        assert (await state.get_data())[APPLICATIONS_OFFSET] == 5
        assert "Vacancy 6" not in ui.text
        assert "Vacancy 1" in ui.text if delete else "Vacancy 2" in ui.text
    run(monkeypatch, scenario, ("Existing", "2026-09-01") if delete else None)


@pytest.mark.parametrize("date_step", [False, True])
@pytest.mark.parametrize("command", [False, True])
def test_cancel(monkeypatch, date_step, command):
    async def scenario(ui, api, state):
        await ui.click("📅 Следующее действие")
        if date_step:
            await text_update(ui, "Draft")
        api.actions = ("External", "2026-09-18")
        if command:
            await text_update(ui, "/cancel")
        else:
            await ui.click("Отмена")
        assert "18.09.2026 — External" in ui.text
        assert api.action_puts == []
        assert await state.get_state() is None
        data = await state.get_data()
        assert data[APPLICATIONS_NEXT_ACTION_DRAFT] is None
        assert data[APPLICATIONS_NEXT_ACTION_TOKEN] is None
        assert data[APPLICATIONS_OFFSET] == 5 and data[APPLICATIONS_FILTER_STATUS] == "interview"
        assert data[APPLICATIONS_MESSAGE_ID] == (101 if date_step else 100)
    run(monkeypatch, scenario)


def test_validation_and_nontext(monkeypatch):
    async def scenario(ui, api, state):
        await ui.click("📅 Следующее действие")
        for value in [" ", "x" * 501, "a\u0000b"]:
            await text_update(ui, value)
            assert await state.get_state() == ApplicationsStates.waiting_for_next_action.state
        await text_update(ui, document={"file_id": "d", "file_unique_id": "d"})
        assert "текст действия" in ui.text
        await text_update(ui, "Draft")
        for value in ["31.02.2026", "12.09.2026 10:00", "2026-09-12", "1.9.2026", "00.01.2026"]:
            await text_update(ui, value)
            assert await state.get_state() == ApplicationsStates.waiting_for_next_action_due_on.state
            assert (await state.get_data())[APPLICATIONS_NEXT_ACTION_DRAFT] == "Draft"
        before = len(ui.sends)
        await text_update(ui, document={"file_id": "d", "file_unique_id": "d"})
        assert "дату в формате" in ui.text
        assert len(ui.sends) == before
        assert api.action_puts == []
    run(monkeypatch, scenario)


def test_initial_get_failure(monkeypatch):
    async def scenario(ui, api, state):
        control = ui.button("📅 Следующее действие")
        api.error = "get"
        await ui.feed(control, message_id=100)
        await text_update(ui, "Must not replace")
        assert await state.get_state() is None
        assert api.action_puts == []
        assert (await state.get_data())[APPLICATIONS_VIEW] == "detail"
        api.error = None
        await ui.feed(control, message_id=100)
        assert "полностью заменены" in ui.text
    run(monkeypatch, scenario, ("Existing", "2026-09-12"))


@pytest.mark.parametrize("delete", [False, True])
@pytest.mark.parametrize("error", ["timeout", "committed_timeout", "500", "404"])
def test_write_errors_do_not_enable_put_retry(monkeypatch, delete, error):
    async def scenario(ui, api, state):
        await ui.click("📅 Следующее действие")
        cancel = ui.button("Отмена")
        if delete:
            await ui.click("Отмена")
            api.action_error = error
            await ui.click("🗑 Удалить следующее действие")
        else:
            await text_update(ui, "New")
            api.action_error = error
            await text_update(ui, "12.09.2026")
        assert await state.get_state() is None
        await ui.feed(cancel, message_id=100)
        await text_update(ui, "12.09.2026")
        assert len(api.action_puts) == (0 if delete else 1)
        assert (await state.get_data())[APPLICATIONS_NEXT_ACTION_DRAFT] is None
        api.action_error = None
        if error == "404":
            await ui.click("⬅️ К списку")
        else:
            assert "Не удалось подтвердить" in ui.text
            await ui.click("К актуальной вакансии")
            assert (await state.get_data())[APPLICATIONS_VIEW] == "detail"
            assert ("New" in ui.text) == (not delete and error == "committed_timeout")
    run(monkeypatch, scenario, ("Existing", "2026-09-18"))


def test_cleanup_and_utf16(monkeypatch):
    async def scenario(ui, api, state):
        api.notes[6] = "😀" * 1000
        api.items[-1]["title"] = "😀" * 4000
        ui.fail_delete, ui.fail_cleanup = True, True
        await ui.click("📅 Следующее действие")
        await text_update(ui, "😀" * 500)
        await text_update(ui, "12.09.2026")
        assert len(ui.text.encode("utf-16-le")) // 2 <= 4096
        assert "😀" * 1000 in ui.text
        assert "12.09.2026 — " + "😀" * 500 in ui.text
        assert ui.deleted == [100, 101] and ui.cleaned == [100, 101]
        assert ui.parse_mode is None
    run(monkeypatch, scenario)


def test_send_failure_after_success(monkeypatch):
    async def scenario(ui, api, state):
        await ui.click("📅 Следующее действие")
        await text_update(ui, "Persisted")
        cancel = ui.button("Отмена")
        async def fail(message, text, **kwargs):
            raise TelegramBadRequest(method=SendMessage(chat_id=456, text=text), message="test")
        monkeypatch.setattr(Message, "answer", fail)
        await text_update(ui, "12.09.2026")
        assert api.actions == ("Persisted", "2026-09-12")
        await ui.feed(cancel, message_id=100)
        await text_update(ui, "12.09.2026")
        assert len(api.action_puts) == 1
        assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] is None
        assert (await state.get_data())[APPLICATIONS_VIEW] == "next_action_render_failed"
    run(monkeypatch, scenario)


@pytest.mark.parametrize("date_step", [False, True])
def test_main_menu(monkeypatch, date_step):
    async def scenario(ui, api, state):
        await ui.click("📅 Следующее действие")
        if date_step:
            await text_update(ui, "Draft")
        old = ui.button("Отмена")
        await ui.menu()
        assert (101 if date_step else 100) in ui.cleaned
        assert await state.get_state() is None
        assert not (await state.get_data()).get(APPLICATIONS_NEXT_ACTION_DRAFT)
        await ui.feed(old, message_id=100)
        await text_update(ui, "12.09.2026")
        assert api.action_puts == []
    run(monkeypatch, scenario)


def test_date_prompt_is_new_canonical_message_and_errors_edit_it(monkeypatch):
    async def scenario(ui, api, state):
        await ui.click("📅 Следующее действие")
        action_prompt_id = (await state.get_data())[APPLICATIONS_MESSAGE_ID]
        action_cancel = ui.button("Отмена")
        edits_before = len(ui.edits)
        sends_before = len(ui.sends)

        await text_update(ui, "Написать HR")

        date_prompt_id = (await state.get_data())[APPLICATIONS_MESSAGE_ID]
        assert action_prompt_id == 100
        assert date_prompt_id == 101
        assert len(ui.sends) == sends_before + 1
        assert len(ui.edits) == edits_before
        assert ui.deleted == [action_prompt_id]
        assert await state.get_state() == ApplicationsStates.waiting_for_next_action_due_on.state
        assert ui.button("Отмена") != action_cancel
        assert (await state.get_data())[APPLICATIONS_NEXT_ACTION_DRAFT] == "Написать HR"
        assert (await state.get_data())[APPLICATIONS_OFFSET] == 5
        assert (await state.get_data())[APPLICATIONS_FILTER_STATUS] == "interview"

        await ui.feed(action_cancel, message_id=action_prompt_id)
        assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == date_prompt_id
        assert await state.get_state() == ApplicationsStates.waiting_for_next_action_due_on.state

        for value in ("31.02.2026", "12.09.2026 10:00"):
            sends_before = len(ui.sends)
            edits_before = len(ui.edits)
            await text_update(ui, value)
            assert len(ui.sends) == sends_before
            assert len(ui.edits) == edits_before + 1
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == date_prompt_id
            assert await state.get_state() == ApplicationsStates.waiting_for_next_action_due_on.state
            assert (await state.get_data())[APPLICATIONS_NEXT_ACTION_DRAFT] == "Написать HR"
            assert ui.button("Отмена")
            assert "⚠️ Дата некорректна" in ui.text
            assert api.action_puts == []

        sends_before = len(ui.sends)
        edits_before = len(ui.edits)
        await text_update(ui, document={"file_id": "d", "file_unique_id": "d"})
        assert len(ui.sends) == sends_before
        assert len(ui.edits) == edits_before + 1
        assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == date_prompt_id
        assert await state.get_state() == ApplicationsStates.waiting_for_next_action_due_on.state
        assert (await state.get_data())[APPLICATIONS_NEXT_ACTION_DRAFT] == "Написать HR"
        assert ui.button("Отмена")
        assert api.action_puts == []
    run(monkeypatch, scenario)


def test_date_prompt_send_failure_restores_action_input(monkeypatch):
    async def scenario(ui, api, state):
        await ui.click("📅 Следующее действие")
        old_message_id = (await state.get_data())[APPLICATIONS_MESSAGE_ID]
        old_cancel = ui.button("Отмена")
        original_answer = Message.answer
        fail_date_prompt = True

        async def answer(message, text, **kwargs):
            if fail_date_prompt and text.startswith("Когда это сделать?"):
                raise TelegramBadRequest(
                    method=SendMessage(chat_id=456, text=text), message="send failed"
                )
            return await original_answer(message, text, **kwargs)

        monkeypatch.setattr(Message, "answer", answer)
        await text_update(ui, "First action")

        data = await state.get_data()
        assert data[APPLICATIONS_MESSAGE_ID] == old_message_id
        assert data[APPLICATIONS_VIEW] == "next_action_input"
        assert data[APPLICATIONS_NEXT_ACTION_DRAFT] is None
        assert data[APPLICATIONS_NEXT_ACTION_TOKEN] == old_cancel.rsplit(":", 1)[1]
        assert await state.get_state() == ApplicationsStates.waiting_for_next_action.state
        assert ui.deleted == []
        assert ui.button("Отмена") == old_cancel
        assert data[APPLICATIONS_OFFSET] == 5 and data[APPLICATIONS_FILTER_STATUS] == "interview"
        assert api.action_puts == []

        fail_date_prompt = False
        await text_update(ui, "Retry action")
        assert await state.get_state() == ApplicationsStates.waiting_for_next_action_due_on.state
        assert (await state.get_data())[APPLICATIONS_NEXT_ACTION_DRAFT] == "Retry action"
        assert api.action_puts == []
    run(monkeypatch, scenario)


def test_date_prompt_send_failure_keeps_old_cancel_usable(monkeypatch):
    async def scenario(ui, api, state):
        await ui.click("📅 Следующее действие")
        old_cancel = ui.button("Отмена")
        original_answer = Message.answer

        async def fail_date_prompt(message, text, **kwargs):
            if text.startswith("Когда это сделать?"):
                raise TelegramBadRequest(
                    method=SendMessage(chat_id=456, text=text), message="send failed"
                )
            return await original_answer(message, text, **kwargs)

        monkeypatch.setattr(Message, "answer", fail_date_prompt)
        await text_update(ui, "Action")
        await ui.feed(old_cancel, message_id=100)
        assert await state.get_state() is None
        assert (await state.get_data())[APPLICATIONS_VIEW] == "detail"
        assert api.action_puts == []
    run(monkeypatch, scenario)


def test_date_prompt_is_canonical_before_old_prompt_cleanup(monkeypatch):
    async def scenario(ui, api, state):
        await ui.click("📅 Следующее действие")
        ui.fail_delete = True
        original_delete = Bot.delete_message

        async def delete_message(bot, **kwargs):
            assert kwargs["message_id"] == 100
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == 101
            assert await state.get_state() == ApplicationsStates.waiting_for_next_action_due_on.state
            return await original_delete(bot, **kwargs)

        monkeypatch.setattr(Bot, "delete_message", delete_message)
        await text_update(ui, "Action")
        assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == 101
        assert await state.get_state() == ApplicationsStates.waiting_for_next_action_due_on.state
        assert ui.button("Отмена")
        assert ui.deleted == [100] and ui.cleaned == [100]
        assert api.action_puts == []
    run(monkeypatch, scenario)


@pytest.mark.parametrize("date_step", [False, True])
def test_cancel_get_failure_discards_draft_and_recovery(monkeypatch, date_step):
    async def scenario(ui, api, state):
        await ui.click("📅 Следующее действие")
        if date_step:
            await text_update(ui, "Draft")
        old = ui.button("Отмена")
        api.error = "get"
        await ui.click("Отмена")
        assert await state.get_state() is None
        assert (await state.get_data())[APPLICATIONS_NEXT_ACTION_DRAFT] is None
        await ui.feed(old, message_id=100)
        await text_update(ui, "12.09.2026")
        assert api.action_puts == []
        api.error = None
        await ui.click("К актуальной вакансии")
        assert (await state.get_data())[APPLICATIONS_VIEW] == "detail"
    run(monkeypatch, scenario)


@pytest.mark.parametrize("stage", ["open", "save", "delete"])
def test_user_resolution_failure(monkeypatch, stage):
    async def scenario(ui, api, state):
        control = ui.button("📅 Следующее действие")
        if stage != "open":
            await ui.click("📅 Следующее действие")
            if stage == "save":
                await text_update(ui, "Draft")
            else:
                await ui.click("Отмена")
        async def fail(actor):
            raise httpx.ReadTimeout("user resolution")
        api.create_or_get_user = fail
        if stage == "open":
            await ui.feed(control, message_id=100)
        elif stage == "save":
            await text_update(ui, "12.09.2026")
        else:
            await ui.click("🗑 Удалить следующее действие")
        await text_update(ui, "12.09.2026")
        assert await state.get_state() is None
        assert api.action_puts == [] and api.action_deletes == []
    run(monkeypatch, scenario, ("Existing", "2026-09-12"))
