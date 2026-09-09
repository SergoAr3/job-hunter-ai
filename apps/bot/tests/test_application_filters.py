"""Exercise rendered controls through the production dispatcher and event isolation."""
import asyncio
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update, User

from test_applications import DispatcherApi, _applications_callback_update, _dispatcher_state
import app.main as main_module
from app.applications import (
    APPLICATIONS_BUTTON, APPLICATIONS_EMPTY_MESSAGE, APPLICATIONS_FILTER_STATUS,
    APPLICATIONS_FILTER_VIEW, APPLICATIONS_LIST_TOKEN, APPLICATIONS_MESSAGE_ID,
    APPLICATIONS_OFFSET, APPLICATIONS_SEARCH_QUERY, APPLICATIONS_SEARCH_TOKEN,
    APPLICATIONS_SORT, APPLICATIONS_SORT_VIEW, ApplicationsStates,
    APPLICATIONS_STATUS_TOKEN, APPLICATIONS_VIEW, SORT_LABELS, STATUS_LABELS,
)


class FilterApi(DispatcherApi):
    def __init__(self, count=12):
        super().__init__()
        self.items = [{"app_id": index, "title": f"Vacancy {index}", "status": "saved"}
                      for index in range(1, count + 1)]
        self.queries = []
        self.search_queries = []
        self.sort_queries = []
        self.puts = []
        self.fail_list = False

    async def list_applications(
        self, user_id, *, limit, offset, status=None, q=None, sort="newest",
    ):
        self.queries.append((user_id, status, limit, offset))
        self.search_queries.append(q)
        self.sort_queries.append(sort)
        if self.fail_list:
            raise httpx.ReadTimeout("test")
        items = [dict(item) for item in self.items if (status is None or item["status"] == status)
                 and (q is None or q.lower() in item["title"].lower())]
        if sort == "oldest":
            items.reverse()
        elif sort == "next_action":
            items.sort(key=lambda item: (
                item.get("next_action_due_on") is None,
                item.get("next_action_due_on") or "",
                -int(item["app_id"]),
            ))
        return {"items": items[offset:offset + limit], "has_next": len(items) > offset + limit}

    async def get_application(self, user_id, app_id):
        self.detail_calls.append((user_id, app_id))
        item = next(item for item in self.items if item["app_id"] == app_id)
        return {"application": {"id": app_id, "status": item["status"]},
                "job": {"title": item["title"], "workplace_type": "remote"}}

    async def put_application_status(self, user_id, app_id, status):
        self.puts.append((user_id, app_id, status))
        next(item for item in self.items if item["app_id"] == app_id)["status"] = status
        return await self.get_application(user_id, app_id)


class UI:
    def __init__(self, bot, api):
        self.bot, self.api = bot, api
        self.text, self.markup = "", None
        self.parse_mode = None
        self.message_id = 0
        self.sends, self.edits, self.cleaned, self.deleted, self.acks = [], [], [], [], []
        self.counter = 0
        self.fail_edit = False
        self.fail_send = False
        self.fail_cleanup = False
        self.fail_delete = False
        self.not_modified = False

    async def send(self, message, text, **kwargs):
        if self.fail_send:
            raise TelegramBadRequest(
                method=SendMessage(chat_id=456, text=text), message="send failed"
            )
        self.message_id = 100 + len(self.sends)
        self.text, self.markup, self.parse_mode = text, kwargs.get("reply_markup"), kwargs.get("parse_mode")
        self.sends.append(self.message_id)
        return SimpleNamespace(message_id=self.message_id)

    async def edit(self, message, text, **kwargs):
        if self.fail_edit or self.not_modified:
            raise TelegramBadRequest(method=EditMessageText(chat_id=456, message_id=message.message_id, text=text),
                                     message="message is not modified" if self.not_modified else "edit failed")
        self.text, self.markup, self.parse_mode = text, kwargs.get("reply_markup"), kwargs.get("parse_mode")
        self.edits.append(message.message_id)

    async def cleanup(self, **kwargs):
        self.cleaned.append(kwargs["message_id"])
        if self.fail_cleanup:
            raise TelegramBadRequest(method=EditMessageText(chat_id=456, message_id=1, text="x"), message="cleanup failed")

    async def delete(self, **kwargs):
        self.deleted.append(kwargs["message_id"])
        if self.fail_delete:
            raise TelegramBadRequest(method=EditMessageText(chat_id=456, message_id=1, text="x"), message="delete failed")

    async def ack(self, callback, **kwargs):
        self.acks.append(callback.id)

    def button(self, text):
        return next(button.callback_data for row in self.markup.inline_keyboard for button in row if button.text == text)

    async def feed(self, data, message_id=None):
        self.counter += 1
        await main_module.dp.feed_update(self.bot, _applications_callback_update(
            data, update_id=self.counter, message_id=self.message_id if message_id is None else message_id,
        ))

    async def click(self, text):
        await self.feed(self.button(text))

    async def menu(self, text=APPLICATIONS_BUTTON):
        self.counter += 1
        await main_module.dp.feed_update(self.bot, Update(update_id=self.counter, message=Message(
            message_id=1000 + self.counter, date=datetime.now(), chat=Chat(id=456, type="private"),
            from_user=User(id=123, is_bot=False, first_name="Anna"), text=text,
        )))

    async def text_input(self, text):
        self.counter += 1
        await main_module.dp.feed_update(self.bot, Update(update_id=self.counter, message=Message(
            message_id=2000 + self.counter, date=datetime.now(), chat=Chat(id=456, type="private"),
            from_user=User(id=123, is_bot=False, first_name="Anna"), text=text,
        )))

    async def cancel(self):
        self.counter += 1
        await main_module.dp.feed_update(self.bot, Update(update_id=self.counter, message=Message(
            message_id=2000 + self.counter, date=datetime.now(), chat=Chat(id=456, type="private"),
            from_user=User(id=123, is_bot=False, first_name="Anna"), text="/cancel",
            entities=[MessageEntity(type="bot_command", offset=0, length=7)],
        )))


async def setup(monkeypatch, count=12):
    bot = Bot("123456:applications-test-token")
    api = FilterApi(count)
    ui = UI(bot, api)
    async def send(message, text, **kwargs):
        return await ui.send(message, text, **kwargs)
    async def edit(message, text, **kwargs):
        return await ui.edit(message, text, **kwargs)
    async def canonical_edit(_bot, *, chat_id, message_id, text, **kwargs):
        assert chat_id == 456 and message_id == ui.message_id
        ui.text, ui.markup, ui.parse_mode = text, kwargs.get("reply_markup"), kwargs.get("parse_mode")
    async def cleanup(_bot, **kwargs):
        return await ui.cleanup(**kwargs)
    async def delete(_bot, **kwargs):
        return await ui.delete(**kwargs)
    async def ack(callback, **kwargs):
        return await ui.ack(callback, **kwargs)
    monkeypatch.setattr(main_module, "api_client", api)
    monkeypatch.setattr(Message, "answer", send)
    monkeypatch.setattr(Message, "edit_text", edit)
    monkeypatch.setattr(Bot, "edit_message_text", canonical_edit)
    monkeypatch.setattr(Bot, "edit_message_reply_markup", cleanup)
    monkeypatch.setattr(Bot, "delete_message", delete)
    monkeypatch.setattr(CallbackQuery, "answer", ack)
    state = await _dispatcher_state(bot, {})
    return ui, api, state


@pytest.mark.parametrize(
    ("choice", "sort"),
    [
        ("✓ 🆕 Сначала новые", "newest"),
        ("🕰 Сначала старые", "oldest"),
        ("📅 Ближайшее действие", "next_action"),
    ],
)
def test_dispatcher_sort_picker_selects_each_mode_and_resets_offset(
    monkeypatch, choice, sort,
):
    async def scenario():
        ui, api, state = await setup(monkeypatch, count=7)
        try:
            await ui.menu()
            await ui.click("Вперёд ➡️")
            await state.update_data({
                APPLICATIONS_SEARCH_QUERY: "Vacancy",
                APPLICATIONS_FILTER_STATUS: "saved",
            })
            await ui.click("Сортировка: Сначала новые")
            assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_SORT_VIEW
            assert ui.button("✓ 🆕 Сначала новые")
            before = len(api.queries)
            await ui.click(choice)
            data = await state.get_data()
            assert data[APPLICATIONS_SORT] == sort
            assert data[APPLICATIONS_OFFSET] == 0
            assert data[APPLICATIONS_SEARCH_QUERY] == "Vacancy"
            assert data[APPLICATIONS_FILTER_STATUS] == "saved"
            assert api.sort_queries[-1] == sort
            assert api.queries[-1] == (4, "saved", 5, 0)
            assert len(api.queries) == before + 1
            assert ui.button(f"Сортировка: {SORT_LABELS[sort]}")
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_sort_survives_list_search_filter_detail_and_match(monkeypatch):
    async def scenario():
        ui, api, state = await setup(monkeypatch, count=7)
        try:
            await ui.menu()
            await ui.click("Сортировка: Сначала новые")
            await ui.click("🕰 Сначала старые")
            await ui.click("Вперёд ➡️")
            assert api.sort_queries[-1] == "oldest"
            await ui.click("🔎 Поиск")
            await ui.text_input("Vacancy")
            assert api.sort_queries[-1] == "oldest"
            await ui.click("✖️ Сбросить поиск")
            assert api.sort_queries[-1] == "oldest"
            await ui.click("Фильтр: Все")
            await ui.click("Сохранена")
            assert api.sort_queries[-1] == "oldest"
            await ui.click("Vacancy 7")
            await ui.click("⬅️ К списку")
            assert api.sort_queries[-1] == "oldest"
            await ui.click("Vacancy 7")
            await ui.click("🔎 Почему подходит?")
            await ui.click("⬅️ К вакансии")
            assert (await state.get_data())[APPLICATIONS_SORT] == "oldest"
            await ui.click("🔎 Почему подходит?")
            await ui.click("📋 К списку")
            assert api.sort_queries[-1] == "oldest"
            assert (await state.get_data())[APPLICATIONS_SORT] == "oldest"
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_sort_cancel_button_and_command_preserve_context(monkeypatch):
    async def scenario():
        ui, api, state = await setup(monkeypatch, count=7)
        try:
            await ui.menu()
            await ui.click("Вперёд ➡️")
            await state.update_data({APPLICATIONS_SEARCH_QUERY: "Vacancy"})
            await ui.click("Сортировка: Сначала новые")
            await ui.click("Отмена")
            data = await state.get_data()
            assert data[APPLICATIONS_SORT] == "newest"
            assert data[APPLICATIONS_OFFSET] == 5
            assert data[APPLICATIONS_SEARCH_QUERY] == "Vacancy"
            assert api.sort_queries[-1] == "newest"

            await ui.click("Сортировка: Сначала новые")
            await ui.cancel()
            data = await state.get_data()
            assert data[APPLICATIONS_SORT] == "newest"
            assert data[APPLICATIONS_OFFSET] == 5
            assert data[APPLICATIONS_SEARCH_QUERY] == "Vacancy"
            assert data[APPLICATIONS_VIEW] == "list"
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_sort_stale_duplicate_and_api_failure_are_safe(monkeypatch):
    async def scenario():
        ui, api, state = await setup(monkeypatch, count=7)
        try:
            await ui.menu()
            await ui.click("Вперёд ➡️")
            await ui.click("Сортировка: Сначала новые")
            choice = ui.button("🕰 Сначала старые")
            old_id = ui.message_id
            api.fail_list = True
            await ui.feed(choice)
            data = await state.get_data()
            assert data[APPLICATIONS_SORT] == "newest"
            assert data[APPLICATIONS_OFFSET] == 5
            assert data[APPLICATIONS_VIEW] == APPLICATIONS_SORT_VIEW
            assert "Не удалось загрузить" in ui.text
            assert ui.button("Отмена")

            api.fail_list = False
            await asyncio.gather(ui.feed(choice), ui.feed(choice))
            assert api.sort_queries[-1] == "oldest"
            successful_calls = len(api.queries)
            await ui.feed(choice)
            await ui.feed(choice, message_id=old_id + 999)
            assert len(api.queries) == successful_calls
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


@pytest.mark.parametrize(("fail_send", "fail_cleanup"), [(False, False), (False, True), (True, False)])
def test_dispatcher_sort_render_failures_preserve_canonical_rules(
    monkeypatch, fail_send, fail_cleanup,
):
    async def scenario():
        ui, api, state = await setup(monkeypatch, count=7)
        try:
            await ui.menu()
            await ui.click("Сортировка: Сначала новые")
            old_message_id = ui.message_id
            old_token = (await state.get_data())[APPLICATIONS_LIST_TOKEN]
            ui.fail_edit = True
            ui.fail_send = fail_send
            ui.fail_cleanup = fail_cleanup
            await ui.click("🕰 Сначала старые")
            data = await state.get_data()
            if fail_send:
                assert data[APPLICATIONS_MESSAGE_ID] == old_message_id
                assert data[APPLICATIONS_LIST_TOKEN] == old_token
                assert data[APPLICATIONS_VIEW] == APPLICATIONS_SORT_VIEW
                assert data[APPLICATIONS_SORT] == "newest"
                assert ui.cleaned == []
            else:
                assert data[APPLICATIONS_MESSAGE_ID] != old_message_id
                assert data[APPLICATIONS_VIEW] == "list"
                assert data[APPLICATIONS_SORT] == "oldest"
                assert old_message_id in ui.cleaned
                assert ui.button("Сортировка: Сначала старые")
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_sort_picker_is_available_when_empty_and_main_menu_resets_it(monkeypatch):
    async def scenario():
        ui, api, state = await setup(monkeypatch, count=0)
        try:
            await ui.menu()
            await ui.click("Сортировка: Сначала новые")
            stale_choice = ui.button("🕰 Сначала старые")
            stale_id = ui.message_id
            await ui.menu("💼 Добавить вакансию")
            assert (await state.get_data()).get(APPLICATIONS_SORT) is None
            calls = len(api.queries)
            await ui.feed(stale_choice, message_id=stale_id)
            assert len(api.queries) == calls
            await ui.menu()
            assert (await state.get_data())[APPLICATIONS_SORT] == "newest"
            assert ui.button("Сортировка: Сначала новые")
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("status", [None, *STATUS_LABELS])
def test_dispatcher_filter_picker_choices_and_reset(monkeypatch, status):
    async def scenario():
        ui, api, state = await setup(monkeypatch)
        try:
            await ui.menu()
            assert api.queries == [(4, None, 5, 0)]
            assert "Статус: Все" in ui.text
            await ui.click("Вперёд ➡️")
            await ui.click("Фильтр: Все")
            assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_FILTER_VIEW
            assert [b.text for row in ui.markup.inline_keyboard for b in row] == [
                "✓ Все", *STATUS_LABELS.values(), "⬅️ Назад",
            ]
            assert len(api.queries) == 2  # picker is local UI
            await ui.click(STATUS_LABELS[status] if status else "✓ Все")
            data = await state.get_data()
            assert data[APPLICATIONS_FILTER_STATUS] == status
            assert data[APPLICATIONS_OFFSET] == 0
            assert api.queries[-1] == (4, status, 5, 0)
            label = STATUS_LABELS[status] if status else "Все"
            if status not in (None, "saved"):
                assert ui.text == f"Вакансий со статусом «{label}» пока нет."
                assert ui.parse_mode is None
            await ui.click(f"Фильтр: {label}")
            assert ui.button(f"✓ {label}")
            await ui.click("⬅️ Назад")
            assert api.queries[-1] == (4, status, 5, 0)
            assert ui.sends == [100]
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == 100
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_filtered_navigation_and_status_change_reloads_api(monkeypatch):
    async def scenario():
        ui, api, state = await setup(monkeypatch, count=6)
        try:
            await ui.menu()
            await state.update_data({APPLICATIONS_SEARCH_QUERY: "Vacancy"})
            await ui.click("Фильтр: Все")
            await ui.click("Сохранена")
            await ui.click("Вперёд ➡️")
            await ui.click("Фильтр: Сохранена")
            await ui.click("⬅️ Назад")
            assert api.queries[-1] == (4, "saved", 5, 5)
            await ui.click("⬅️ Назад")
            assert api.queries[-1] == (4, "saved", 5, 0)
            await ui.click("Вперёд ➡️")
            await ui.click("Vacancy 6")
            await ui.click("⬅️ К списку")
            assert api.queries[-1] == (4, "saved", 5, 5)
            await ui.click("Vacancy 6")
            await ui.click("🔎 Почему подходит?")
            await ui.click("⬅️ К вакансии")
            await ui.click("🔎 Почему подходит?")
            await ui.click("📋 К списку")
            assert api.queries[-1] == (4, "saved", 5, 5)
            await ui.click("Vacancy 6")
            await ui.click("Изменить статус")
            await ui.click("⬅️ Назад")
            assert (await state.get_data())[APPLICATIONS_FILTER_STATUS] == "saved"
            assert (await state.get_data())[APPLICATIONS_OFFSET] == 5
            await ui.click("Изменить статус")
            old_status_callback = ui.button("Откликнулся")
            await asyncio.gather(ui.feed(old_status_callback), ui.feed(old_status_callback))
            assert api.puts == [(4, 6, "applied")]
            assert "Статус: Откликнулся" in ui.text
            assert (await state.get_data())[APPLICATIONS_STATUS_TOKEN] is None
            before = len(api.queries)
            await ui.click("⬅️ К списку")
            assert api.queries[before:] == [(4, "saved", 5, 5), (4, "saved", 5, 0)]
            assert api.search_queries[1:] == ["Vacancy"] * (len(api.search_queries) - 1)
            assert "Vacancy 6" not in ui.text
            assert "Vacancy 5" in ui.text
            assert (await state.get_data())[APPLICATIONS_FILTER_STATUS] == "saved"
            assert (await state.get_data())[APPLICATIONS_SEARCH_QUERY] == "Vacancy"
            assert (await state.get_data())[APPLICATIONS_OFFSET] == 0
            assert ui.sends == [100]
            assert set(api.create_users) == {123}
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("count", [1, 12])
def test_dispatcher_filtered_empty_page_retreats_until_zero(monkeypatch, count):
    async def scenario():
        ui, api, state = await setup(monkeypatch, count=count)
        try:
            await ui.menu()
            await ui.click("Фильтр: Все")
            await ui.click("Сохранена")
            if count == 12:
                await ui.click("Вперёд ➡️")
                await ui.click("Вперёд ➡️")
            await ui.click(f"Vacancy {11 if count == 12 else 1}")
            await ui.click("Изменить статус")
            await ui.click("Откликнулся")
            # Other records can also change outside this interaction.
            for item in api.items:
                item["status"] = "applied"
            before = len(api.queries)
            await ui.click("⬅️ К списку")
            offsets = [10, 5, 0] if count == 12 else [0]
            assert api.queries[before:] == [(4, "saved", 5, offset) for offset in offsets]
            assert ui.text == "Вакансий со статусом «Сохранена» пока нет."
            assert ui.parse_mode is None
            assert (await state.get_data())[APPLICATIONS_OFFSET] == 0
            await ui.click("Фильтр: Сохранена")
            await ui.click("Все")
            assert "Vacancy 1" in ui.text
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_stale_and_duplicate_list_filter_callbacks_are_noop(monkeypatch):
    async def scenario():
        ui, api, state = await setup(monkeypatch)
        try:
            await ui.menu()
            old_open = ui.button("Vacancy 1")
            old_page = ui.button("Вперёд ➡️")
            old_filter = ui.button("Фильтр: Все")
            await ui.feed(old_filter)
            old_choice = ui.button("Сохранена")
            old_back = ui.button("⬅️ Назад")
            await asyncio.gather(ui.feed(old_choice), ui.feed(old_choice))
            assert api.queries == [(4, None, 5, 0), (4, "saved", 5, 0)]
            page = ui.button("Вперёд ➡️")
            await asyncio.gather(ui.feed(page), ui.feed(page))
            assert api.queries[-1] == (4, "saved", 5, 5)
            assert len(api.queries) == 3
            before = (len(api.create_users), len(api.queries), len(ui.edits))
            for stale in (old_open, old_page, old_filter, old_choice, old_back, page,
                          "applications:open:1:0", "applications:page:0"):
                await ui.feed(stale)
            assert (len(api.create_users), len(api.queries), len(ui.edits)) == before
            assert api.detail_calls == []
            await ui.click("Фильтр: Сохранена")
            before = (len(api.create_users), len(api.queries), len(ui.edits))
            await ui.feed(old_choice)
            await ui.feed(old_back)
            assert (len(api.create_users), len(api.queries), len(ui.edits)) == before
            token = (await state.get_data())[APPLICATIONS_LIST_TOKEN]
            await ui.feed(f"applications:list:{token}:set:invalid")
            assert (await state.get_data())[APPLICATIONS_LIST_TOKEN] == token
            pending = ui.button("Оффер")
            old_id = ui.message_id
            ui.fail_cleanup = True
            await ui.menu("💼 Добавить вакансию")
            assert (await state.get_data()).get(APPLICATIONS_FILTER_STATUS) is None
            assert (await state.get_data()).get(APPLICATIONS_LIST_TOKEN) is None
            await ui.feed(pending, message_id=old_id)
            assert len(api.queries) == before[1]
            await ui.menu()
            assert api.queries[-1] == (4, None, 5, 0)
            assert (await state.get_data())[APPLICATIONS_OFFSET] == 0
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("fail_cleanup", [False, True])
def test_dispatcher_filter_edit_fallback_keeps_one_active_message(monkeypatch, fail_cleanup):
    async def scenario():
        ui, api, state = await setup(monkeypatch)
        try:
            await ui.menu()
            old_filter = ui.button("Фильтр: Все")
            ui.fail_edit, ui.fail_cleanup = True, fail_cleanup
            await ui.feed(old_filter)
            assert ui.cleaned == [100]
            assert ui.sends == [100, 101]
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == 101
            choice = ui.button("Сохранена")
            before = len(api.queries)
            await ui.feed(old_filter, message_id=100)
            await ui.feed(choice, message_id=100)
            assert len(api.queries) == before
            ui.fail_edit = False
            await ui.feed(choice)
            assert api.queries[-1] == (4, "saved", 5, 0)
            assert ui.sends == [100, 101]
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_empty_all_and_list_http_failure(monkeypatch):
    async def scenario():
        ui, api, state = await setup(monkeypatch, count=0)
        try:
            await ui.menu()
            assert ui.text == APPLICATIONS_EMPTY_MESSAGE
            assert ui.button("💼 Добавить вакансию")
            await ui.click("Фильтр: Все")
            pending = ui.button("Сохранена")
            api.fail_list = True
            await ui.feed(pending)
            assert "Не удалось загрузить" in ui.text
            assert "пока нет" not in ui.text
            assert api.queries[-1] == (4, "saved", 5, 0)
            await ui.feed(pending)
            assert len(api.queries) == 2
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_nonempty_filtered_list_does_not_use_html_parse_mode(monkeypatch):
    async def scenario():
        ui, api, state = await setup(monkeypatch, count=1)
        api.items[0].update({
            "title": "Senior <Python>",
            "company": "ACME <Ltd>",
            "location": "<Remote>",
        })
        try:
            await ui.menu()
            await ui.click("Фильтр: Все")
            await ui.click("Сохранена")
            assert "Senior <Python>" in ui.text
            assert "ACME <Ltd>" in ui.text
            assert "<Remote>" in ui.text
            assert ui.parse_mode is None
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_search_preserves_query_across_paging_and_status_and_reset(monkeypatch):
    async def scenario():
        ui, api, state = await setup(monkeypatch, count=6)
        try:
            await ui.menu()
            before = len(api.queries)
            await ui.click("🔎 Поиск")
            assert await state.get_state() == ApplicationsStates.waiting_for_search.state
            assert "Отправь название" in ui.text
            await ui.text_input("  \t ")
            assert await state.get_state() == ApplicationsStates.waiting_for_search.state
            assert len(api.queries) == before
            assert "непустое" in ui.text
            await ui.text_input("Vacancy")
            assert (await state.get_data())[APPLICATIONS_SEARCH_QUERY] == "Vacancy"
            assert api.search_queries[-1] == "Vacancy"
            assert "Поиск: Vacancy" in ui.text
            await ui.click("Вперёд ➡️")
            assert api.search_queries[-1] == "Vacancy"
            await ui.click("Фильтр: Все")
            await ui.click("Сохранена")
            assert (await state.get_data())[APPLICATIONS_FILTER_STATUS] == "saved"
            assert api.search_queries[-1] == "Vacancy"
            await ui.click("✖️ Сбросить поиск")
            data = await state.get_data()
            assert data[APPLICATIONS_SEARCH_QUERY] is None
            assert data[APPLICATIONS_FILTER_STATUS] == "saved"
            assert data[APPLICATIONS_OFFSET] == 0
            assert api.search_queries[-1] is None
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_search_cancel_non_text_failure_and_stale_callbacks(monkeypatch):
    async def scenario():
        ui, api, state = await setup(monkeypatch, count=1)
        try:
            await ui.menu()
            await ui.click("🔎 Поиск")
            cancel = ui.button("Отмена")
            search_token = (await state.get_data())[APPLICATIONS_SEARCH_TOKEN]
            await ui.feed(cancel)
            assert (await state.get_data())[APPLICATIONS_SEARCH_QUERY] is None
            await ui.click("🔎 Поиск")
            await ui.text_input("query")
            # The fake API has no matching row, but a successful empty page commits q.
            assert (await state.get_data())[APPLICATIONS_SEARCH_QUERY] == "query"
            await ui.click("✖️ Сбросить поиск")
            assert (await state.get_data())[APPLICATIONS_SEARCH_QUERY] is None
            await ui.click("🔎 Поиск")
            before = (await state.get_data())[APPLICATIONS_SEARCH_QUERY]
            api.fail_list = True
            await ui.text_input("pending")
            assert await state.get_state() == ApplicationsStates.waiting_for_search.state
            assert (await state.get_data())[APPLICATIONS_SEARCH_QUERY] == before
            assert "Не удалось выполнить поиск" in ui.text
            queries = len(api.queries)
            await ui.feed(f"applications:search_cancel:{search_token}")
            assert len(api.queries) == queries
            await ui.text_input("x")
            assert len(api.queries) == queries + 1
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_search_empty_states_non_text_and_command_cancel(monkeypatch):
    async def scenario():
        ui, api, state = await setup(monkeypatch, count=0)
        try:
            await ui.menu()
            await ui.click("🔎 Поиск")
            await ui.text_input("Python")
            assert ui.text == "По запросу «Python» вакансий не найдено."
            assert ui.button("✖️ Сбросить поиск")
            await ui.click("Фильтр: Все")
            await ui.click("Сохранена")
            assert "По запросу «Python» среди вакансий со статусом «Сохранена»" in ui.text
            await ui.click("🔎 Поиск")
            before = len(api.queries)
            await ui.text_input(None)
            assert await state.get_state() == ApplicationsStates.waiting_for_search.state
            assert len(api.queries) == before
            await ui.cancel()
            assert await state.get_state() is None
            assert (await state.get_data())[APPLICATIONS_SEARCH_QUERY] == "Python"
            assert api.search_queries[-1] == "Python"
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_search_prompt_edit_fallback_keeps_one_canonical_context(monkeypatch):
    async def scenario():
        ui, _api, state = await setup(monkeypatch, count=1)
        try:
            await ui.menu()
            old_message_id = ui.message_id
            ui.fail_edit = True
            await ui.click("🔎 Поиск")
            assert ui.sends == [100, 101]
            assert ui.cleaned == [old_message_id]
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == 101
            assert await state.get_state() == ApplicationsStates.waiting_for_search.state
            old_search = next(
                button.callback_data
                for row in ui.markup.inline_keyboard for button in row if button.text == "Отмена"
            )
            await ui.feed(old_search, message_id=old_message_id)
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == 101
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("count", [1, 0])
def test_dispatcher_successful_search_replaces_and_cleans_old_prompt(monkeypatch, count):
    async def scenario():
        ui, _api, state = await setup(monkeypatch, count=count)
        try:
            await ui.menu()
            await ui.click("🔎 Поиск")
            old_prompt_id = ui.message_id
            old_cancel = ui.button("Отмена")
            await ui.text_input("Vacancy")
            data = await state.get_data()
            assert data[APPLICATIONS_MESSAGE_ID] != old_prompt_id
            assert data[APPLICATIONS_MESSAGE_ID] == ui.message_id
            assert ui.deleted == [old_prompt_id]
            assert ui.cleaned == []
            if count:
                assert "Поиск: Vacancy" in ui.text
            else:
                assert ui.text == "По запросу «Vacancy» вакансий не найдено."
            before = (len(ui.deleted), len(ui.cleaned), len(_api.queries))
            await ui.feed(old_cancel, message_id=old_prompt_id)
            assert (len(ui.deleted), len(ui.cleaned), len(_api.queries)) == before
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("fail_cleanup", [False, True])
def test_dispatcher_search_prompt_delete_failure_falls_back_without_breaking_list(monkeypatch, fail_cleanup):
    async def scenario():
        ui, _api, state = await setup(monkeypatch, count=1)
        try:
            await ui.menu()
            await ui.click("🔎 Поиск")
            old_prompt_id = ui.message_id
            ui.fail_delete, ui.fail_cleanup = True, fail_cleanup
            await ui.text_input("Vacancy")
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == ui.message_id
            assert ui.message_id != old_prompt_id
            assert "Поиск: Vacancy" in ui.text
            assert ui.deleted == [old_prompt_id]
            assert ui.cleaned == [old_prompt_id]
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_failed_search_keeps_old_prompt_active_and_repeat_search_cleans_each_prompt(monkeypatch):
    async def scenario():
        ui, api, state = await setup(monkeypatch, count=1)
        try:
            await ui.menu()
            await ui.click("🔎 Поиск")
            first_prompt_id = ui.message_id
            first_cancel = ui.button("Отмена")
            api.fail_list = True
            await ui.text_input("pending")
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == first_prompt_id
            assert await state.get_state() == ApplicationsStates.waiting_for_search.state
            assert ui.button("Отмена") == first_cancel
            assert ui.deleted == []
            api.fail_list = False
            await ui.text_input("Vacancy")
            first_list_id = ui.message_id
            assert ui.deleted == [first_prompt_id]
            await ui.click("🔎 Поиск")
            second_prompt_id = ui.message_id
            assert second_prompt_id == first_list_id
            await ui.text_input("Vacancy 1")
            assert ui.message_id != second_prompt_id
            assert ui.deleted == [first_prompt_id, second_prompt_id]
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == ui.message_id
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_duplicate_open_and_failed_detail_invalidate_list_controls(monkeypatch):
    async def scenario():
        ui, api, state = await setup(monkeypatch)
        try:
            await ui.menu()
            opening = ui.button("Vacancy 1")
            await asyncio.gather(ui.feed(opening), ui.feed(opening))
            assert api.detail_calls == [(4, 1)]
            await ui.click("⬅️ К списку")
            before = len(api.create_users)
            await ui.feed(opening)
            assert len(api.create_users) == before
            async def unavailable(*args):
                raise httpx.ConnectError("test")
            api.get_application = unavailable
            opening = ui.button("Vacancy 1")
            await ui.feed(opening)
            assert "Не удалось загрузить" in ui.text
            assert ui.markup is None  # consumed controls do not remain visually active
            assert (await state.get_data())[APPLICATIONS_LIST_TOKEN] is None
            before = len(api.create_users)
            await ui.feed(opening)
            assert len(api.create_users) == before
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_empty_all_add_button_invalidates_context(monkeypatch):
    async def scenario():
        ui, api, state = await setup(monkeypatch, count=0)
        try:
            await ui.menu()
            old_id = ui.message_id
            add = ui.button("💼 Добавить вакансию")
            picker = ui.button("Фильтр: Все")
            await ui.feed(add)
            assert "Пришли ссылку" in ui.text
            assert old_id in ui.cleaned
            assert (await state.get_data()).get(APPLICATIONS_LIST_TOKEN) is None
            await ui.feed(add, message_id=old_id)
            await ui.feed(picker, message_id=old_id)
            assert len(ui.sends) == 2
            assert len(api.queries) == 1
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_not_modified_keeps_canonical_message(monkeypatch):
    async def scenario():
        ui, api, state = await setup(monkeypatch)
        try:
            await ui.menu()
            await ui.click("Vacancy 1")
            await ui.click("Изменить статус")
            await ui.click("⬅️ Назад")
            assert (await state.get_data())[APPLICATIONS_FILTER_STATUS] is None
            # Telegram reports that the requested message already exists unchanged.
            ui.not_modified = True
            await ui.click("Изменить статус")
            assert ui.sends == [100]
            assert ui.cleaned == []
            assert (await state.get_data())[APPLICATIONS_MESSAGE_ID] == 100
        finally:
            await state.clear()
            await ui.bot.session.close()
    asyncio.run(scenario())
