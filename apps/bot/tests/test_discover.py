import asyncio
import os
from datetime import datetime
from html import unescape
from types import SimpleNamespace

import httpx
import pytest
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update, User

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:discover-test-token")

import app.main as main_module
import app.discover as discover_module
from app.discover import (
    DISCOVER_BUTTON, DiscoverStates, _detail_content, _error_text,
    DESCRIPTION_PREVIEW_LIMIT, REQUIREMENTS_PREVIEW_LIMIT, format_location,
    format_preview, format_salary,
)


class FakeChatActionSender:
    instances: list["FakeChatActionSender"] = []
    fail_on_enter = False

    def __init__(self, *, chat_id: int, bot: object) -> None:
        self.chat_id = chat_id
        self.bot = bot
        self.entered = False
        self.exited = False

    @classmethod
    def typing(cls, *, chat_id: int, bot: object) -> "FakeChatActionSender":
        sender = cls(chat_id=chat_id, bot=bot)
        cls.instances.append(sender)
        return sender

    async def __aenter__(self) -> "FakeChatActionSender":
        self.entered = True
        if self.fail_on_enter:
            raise RuntimeError("typing unavailable")
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        self.exited = True


@pytest.fixture(autouse=True)
def fake_chat_action_sender(monkeypatch):
    FakeChatActionSender.instances = []
    FakeChatActionSender.fail_on_enter = False
    monkeypatch.setattr(discover_module, "ChatActionSender", FakeChatActionSender)


def item(index: int = 0, **changes: object) -> dict[str, object]:
    return {
        "source": "trudvsem", "source_scope": "company", "external_id": f"vacancy-{index}",
        "source_url": f"https://trudvsem.ru/vacancy/card/company/vacancy-{index}",
        "title": f"Python {index}", "company": "Acme", "location": "Казань",
        "description": "Описание " * 200, "requirements_text": "Требования " * 100,
        "workplace_type": "unknown", "salary_min": None, "salary_max": None,
        "salary_currency": "RUB", "salary_text": None,
        "preview_match": {"available": False, "score": None, "verdict": None},
        "already_saved_for_user": False, **changes,
    }


class FakeApi:
    def __init__(self) -> None:
        self.search_calls: list[tuple[str, int]] = []
        self.save_calls: list[str] = []
        self.error: httpx.HTTPError | None = None
        self.save_error: httpx.HTTPError | None = None
        self.saved = False
        self.application_created = True
        self.empty = False
        self.resolved_telegram_ids: list[int] = []
        self.application_user_ids: list[int] = []

    async def create_or_get_user(self, user: User) -> int:
        self.resolved_telegram_ids.append(user.id)
        return user.id + 1000

    async def discover_jobs(self, user_id: int, *, query: str, limit: int, offset: int) -> dict[str, object]:
        self.search_calls.append((query, offset))
        if self.error:
            raise self.error
        items = [] if self.empty else [
            item(i + offset, already_saved_for_user=self.saved and i == 0)
            for i in range(5 if offset == 0 else 1)
        ]
        return {"items": items, "offset": offset,
                "next_offset": 5 if offset == 0 and not self.empty else None}

    async def save_discovered_job(self, user_id: int, *, source: str, source_scope: str, external_id: str) -> dict[str, object]:
        self.save_calls.append(external_id)
        if self.save_error:
            raise self.save_error
        self.saved = True
        return {"application_created": self.application_created, "job_created": self.application_created,
                "application": {"id": 75, "user_id": 31, "job_id": 73}, "job": {"id": 73}}

    async def list_applications(self, user_id: int, *, limit: int, offset: int,
                                status: str | None = None, q: str | None = None,
                                sort: str = "newest") -> dict[str, object]:
        self.application_user_ids.append(user_id)
        return {"items": [], "has_next": False}


def text_update(text: str, update_id: int) -> Update:
    entities = [MessageEntity(type="bot_command", offset=0, length=len(text))] if text.startswith("/") else []
    return Update(update_id=update_id, message=Message(
        message_id=update_id, date=datetime.now(), chat=Chat(id=456, type="private"),
        from_user=User(id=123, is_bot=False, first_name="Анна"), text=text, entities=entities,
    ))


def callback_update(data: str, update_id: int, message_id: int = 100,
                    message_user: User | None = None) -> Update:
    user = User(id=123, is_bot=False, first_name="Анна")
    return Update(update_id=update_id, callback_query=CallbackQuery(
        id=f"callback-{update_id}", from_user=user, chat_instance="chat", data=data,
        message=Message(message_id=message_id, date=datetime.now(),
                        chat=Chat(id=456, type="private"),
                        from_user=message_user or user, text="card"),
    ))


def test_formatters_cover_salary_workplace_matching_and_truncation() -> None:
    assert format_salary(item()) == "Зарплата не указана"
    assert format_salary(item(salary_min=0, salary_max=0)) == "Зарплата не указана"
    assert format_salary(item(salary_min=100000, salary_max=0)) == "от 100000 ₽"
    assert format_salary(item(salary_min=100000, salary_max=150000)) == "100000–150000 ₽"
    assert "Хорошее совпадение" in format_preview({"available": True, "score": 78, "verdict": "high"})
    assert "недостаточно данных" in format_preview({"available": True, "score": None, "verdict": "insufficient_data"})
    assert "заполните профиль" in format_preview({"available": False, "unavailable_reason": "profile_missing_for_preview"})
    text, markup = _detail_content(item(), "abcd", has_next=True)
    assert len(text) < 1600
    assert "офис" not in text and "гибрид" not in text
    assert "💰 Зарплата не указана" in text
    assert "📍 Казань" in text
    assert "Удалённо" in _detail_content(item(workplace_type="remote"), "abcd", has_next=False)[0]
    assert markup.inline_keyboard[-1][0].url.startswith("https://trudvsem.ru/")
    saved_markup = _detail_content(item(already_saved_for_user=True), "abcd", has_next=False)[1]
    assert saved_markup.inline_keyboard[0][0].text == "✅ Уже в моих вакансиях"
    assert all(not str(button.callback_data).endswith(":save") for row in saved_markup.inline_keyboard for button in row)


@pytest.mark.parametrize(("source", "display"), [
    ("Самара", "Самара"),
    ("Свердловская область, г Екатеринбург", "Свердловская область, г Екатеринбург"),
    (
        "Свердловская область, г Екатеринбург, Октябрьская площадь, 7",
        "Свердловская область, г Екатеринбург",
    ),
    (
        " Самарская область ,  г Самара , Приволжский федеральный округ ",
        "Самарская область, г Самара",
    ),
    ("", ""),
    (" , , ", ""),
])
def test_location_formatter_keeps_only_first_two_nonempty_segments(source: str, display: str) -> None:
    assert format_location(source) == display


def test_detail_hides_empty_location_and_displays_compact_location() -> None:
    long_location = (
        "Самарская область, г Самара, Приволжский федеральный округ, "
        "Самарская область, городской округ Самара, Самара"
    )
    text, _ = _detail_content(item(location=long_location), "abcd", has_next=False)
    assert "📍 Самарская область, г Самара" in text
    assert "Приволжский федеральный округ" not in text

    no_location, _ = _detail_content(item(location=" , , "), "abcd", has_next=False)
    assert "📍" not in no_location


def test_detail_preview_truncates_source_text_before_html_escaping() -> None:
    description = "Описание <важное> & полезное " * 40
    requirements = "Требование <Python> & SQL " * 40
    text, _ = _detail_content(item(description=description, requirements_text=requirements), "abcd", has_next=False)

    description_preview = text.split("Описание:\n", 1)[1].split("\n\nТребования:", 1)[0]
    requirements_preview = text.split("Требования:\n", 1)[1].split("\n\n🎯", 1)[0]
    assert description_preview.endswith("…")
    assert requirements_preview.endswith("…")
    assert len(unescape(description_preview)) <= DESCRIPTION_PREVIEW_LIMIT
    assert len(unescape(requirements_preview)) <= REQUIREMENTS_PREVIEW_LIMIT
    assert "&lt;" in description_preview and "&amp;" in description_preview
    assert "&lt;" in requirements_preview and "&amp;" in requirements_preview
    assert "<важное>" not in description_preview
    assert "<Python>" not in requirements_preview


def test_detail_preview_keeps_short_text_and_compact_metadata() -> None:
    text, _ = _detail_content(item(
        title="Python <Developer>",
        company="Acme & Co",
        description="Короткое описание",
        requirements_text="Python и SQL",
        location=None,
        salary_min=120000,
        salary_max=None,
        workplace_type="unknown",
    ), "abcd", has_next=False)

    assert "Python &lt;Developer&gt;" in text
    assert "Acme &amp; Co" in text
    assert "Описание:\nКороткое описание" in text
    assert "Требования:\nPython и SQL" in text
    assert "💰 от 120000 ₽" in text
    assert "📍" not in text
    assert "🌐" not in text


@pytest.mark.parametrize("code, expected", [
    ("source_timeout", "отвечает слишком долго"),
    ("source_unavailable", "временно недоступен"),
    ("source_rate_limited", "временно недоступен"),
    ("source_bad_response", "Не удалось получить вакансии"),
    ("vacancy_not_found", "уже недоступна"),
    ("source_identity_conflict", "безопасно сохранить"),
])
def test_typed_source_errors_are_human_readable(code: str, expected: str) -> None:
    request = httpx.Request("GET", "http://api/discover")
    response = httpx.Response(504, request=request, json={"detail": {"code": code}})
    error = httpx.HTTPStatusError("source failed", request=request, response=response)
    text = _error_text(error, saving=False)
    assert expected in text
    assert code not in text


def test_dispatcher_new_query_creates_loading_card_before_api_and_stales_old_callbacks(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:discover-test-token")
        events: list[str] = []

        class OrderedApi(FakeApi):
            async def discover_jobs(self, *args, **kwargs):
                events.append("api")
                return await super().discover_jobs(*args, **kwargs)

        api = OrderedApi()
        monkeypatch.setattr(main_module, "api_client", api)
        answers: list[tuple[str, object | None]] = []
        edited_message_ids: list[int] = []
        removed_message_ids: list[int] = []
        callback_answers: list[str | None] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            answers.append((text, kwargs.get("reply_markup")))
            events.append("loading")
            return SimpleNamespace(message_id=200)

        async def edit_text(bot_self: Bot, *, message_id: int, text: str, **kwargs: object) -> None:
            edited_message_ids.append(message_id)

        async def edit_markup(bot_self: Bot, *, message_id: int, **kwargs: object) -> None:
            removed_message_ids.append(message_id)

        async def callback_answer(callback: CallbackQuery, text: str | None = None, **kwargs: object) -> None:
            callback_answers.append(text)

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_text", edit_text)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_markup)
        monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
        state = FSMContext(storage=main_module.dp.storage, key=StorageKey(bot_id=bot.id, chat_id=456, user_id=123))
        await state.set_state(DiscoverStates.waiting_for_query)
        await state.set_data({
            "discover_active_message_id": 100,
            "discover_screen_token": "oldtoken",
            "discover_current_screen": "query",
            "discover_current_items": [],
        })
        try:
            await main_module.dp.feed_update(bot, text_update("<Python & backend>", 1))

            assert answers == [("🔎 Ищу вакансии по запросу «&lt;Python &amp; backend&gt;»…", None)]
            assert events[:2] == ["loading", "api"]
            assert removed_message_ids == [100]
            assert edited_message_ids == [200]
            assert (await state.get_data())["discover_active_message_id"] == 200
            assert FakeChatActionSender.instances[0].chat_id == 456
            assert FakeChatActionSender.instances[0].entered is True
            assert FakeChatActionSender.instances[0].exited is True

            await main_module.dp.feed_update(bot, callback_update("discover:oldtoken:menu", 2, message_id=100))
            assert callback_answers[-1] == "Эта кнопка уже неактуальна."
            assert api.search_calls == [("<Python & backend>", 0)]
        finally:
            await state.clear()
            await bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_query_search_survives_typing_failure(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:discover-test-token")
        api = FakeApi()
        monkeypatch.setattr(main_module, "api_client", api)
        FakeChatActionSender.fail_on_enter = True

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(message_id=200)

        async def edit_text(bot_self: Bot, **kwargs: object) -> None:
            return None

        async def edit_markup(bot_self: Bot, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_text", edit_text)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_markup)
        state = FSMContext(storage=main_module.dp.storage, key=StorageKey(bot_id=bot.id, chat_id=456, user_id=123))
        await state.set_state(DiscoverStates.waiting_for_query)
        await state.set_data({"discover_active_message_id": 100, "discover_current_screen": "query"})
        try:
            await main_module.dp.feed_update(bot, text_update("Python", 3))
            assert api.search_calls == [("Python", 0)]
            assert (await state.get_data())["discover_current_screen"] == "list"
        finally:
            await state.clear()
            await bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_discover_lifecycle_and_stale_callbacks(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:discover-test-token")
        api = FakeApi()
        monkeypatch.setattr(main_module, "api_client", api)
        rendered: list[tuple[str, object]] = []
        removed: list[int] = []
        callback_answers: list[str | None] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            rendered.append((text, kwargs.get("reply_markup")))
            return SimpleNamespace(message_id=100 if len(rendered) == 1 else 100 + len(rendered))

        edited_message_ids: list[int] = []

        async def edit_text(bot_self: Bot, *, message_id: int, text: str, **kwargs: object) -> None:
            edited_message_ids.append(message_id)
            rendered.append((text, kwargs.get("reply_markup")))

        async def edit_markup(bot_self: Bot, *, message_id: int, **kwargs: object) -> None:
            removed.append(message_id)

        async def callback_answer(callback: CallbackQuery, text: str | None = None, **kwargs: object) -> None:
            callback_answers.append(text)

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_text", edit_text)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_markup)
        monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
        state = FSMContext(storage=main_module.dp.storage, key=StorageKey(bot_id=bot.id, chat_id=456, user_id=123))
        await state.clear()
        try:
            await main_module.dp.feed_update(bot, text_update(DISCOVER_BUTTON, 1))
            assert await state.get_state() == DiscoverStates.waiting_for_query.state
            assert rendered[-1][0] == "Что ищем?\nНапример: Python backend"
            await main_module.dp.feed_update(bot, text_update("   ", 2))
            assert api.search_calls == []
            await main_module.dp.feed_update(bot, text_update("Python", 3))
            assert api.search_calls == [("Python", 0)]
            assert "5. Python 4" in rendered[-1][0]
            assert edited_message_ids[-1] != 100
            active_message_id = (await state.get_data())["discover_active_message_id"]
            list_token = (await state.get_data())["discover_screen_token"]
            await main_module.dp.feed_update(bot, callback_update(f"discover:{list_token}:open:0", 40, message_id=99))
            assert api.search_calls == [("Python", 0)]
            assert (await state.get_data())["discover_current_screen"] == "list"
            await main_module.dp.feed_update(bot, callback_update(f"discover:{list_token}:open:0", 4, message_id=active_message_id))
            detail_token = (await state.get_data())["discover_screen_token"]
            assert "Описание:" in rendered[-1][0]
            await main_module.dp.feed_update(bot, callback_update(f"discover:{list_token}:open:1", 5, message_id=active_message_id))
            assert callback_answers[-1] == "Эта кнопка уже неактуальна."
            assert api.search_calls == [("Python", 0)]
            await main_module.dp.feed_update(bot, callback_update(f"discover:{detail_token}:save", 6, message_id=active_message_id))
            assert api.save_calls == ["vacancy-0"]
            assert "✅ В моих вакансиях" in str(rendered[-1][1])
            await main_module.dp.feed_update(bot, callback_update(f"discover:{detail_token}:save", 7, message_id=active_message_id))
            assert api.save_calls == ["vacancy-0"]
            saved_token = (await state.get_data())["discover_screen_token"]
            await main_module.dp.feed_update(bot, callback_update(f"discover:{saved_token}:advance", 8, message_id=active_message_id))
            assert (await state.get_data())["discover_selected_index"] == 1
            next_token = (await state.get_data())["discover_screen_token"]
            await main_module.dp.feed_update(bot, callback_update(f"discover:{next_token}:list", 9, message_id=active_message_id))
            assert (await state.get_data())["discover_current_screen"] == "list"
            list_token = (await state.get_data())["discover_screen_token"]
            await main_module.dp.feed_update(bot, callback_update(f"discover:{list_token}:next", 91, message_id=active_message_id))
            assert api.search_calls[-1] == ("Python", 5)
            list_token = (await state.get_data())["discover_screen_token"]
            await main_module.dp.feed_update(bot, callback_update(f"discover:{list_token}:prev", 92, message_id=active_message_id))
            assert api.search_calls[-1] == ("Python", 0)
            list_token = (await state.get_data())["discover_screen_token"]
            await main_module.dp.feed_update(bot, callback_update(f"discover:{list_token}:open:0", 93, message_id=active_message_id))
            assert "✅ Уже в моих вакансиях" in str(rendered[-1][1])
            saved_token = (await state.get_data())["discover_screen_token"]
            bot_actor = User(id=987654, is_bot=True, first_name="Job Hunter AI")
            await main_module.dp.feed_update(bot, callback_update(
                f"discover:{saved_token}:applications", 94,
                message_id=active_message_id, message_user=bot_actor,
            ))
            assert (await state.get_data()).get("discover_screen_token") is None
            assert removed[-1] != 0
            assert api.resolved_telegram_ids
            assert set(api.resolved_telegram_ids) == {123}
            assert api.application_user_ids == [1123]
            assert 987654 not in api.resolved_telegram_ids
            await main_module.dp.feed_update(bot, callback_update(f"discover:{saved_token}:save", 95, message_id=active_message_id))
            assert api.save_calls == ["vacancy-0"]
            await main_module.dp.feed_update(bot, text_update(DISCOVER_BUTTON, 96))
            await main_module.dp.feed_update(bot, text_update("/cancel", 10))
            assert await state.get_state() is None
            assert (await state.get_data()).get("discover_screen_token") is None
            assert removed[-1] != 0
            await main_module.dp.feed_update(bot, callback_update(f"discover:{next_token}:save", 11))
            assert api.save_calls == ["vacancy-0"]
            await main_module.dp.feed_update(bot, text_update(DISCOVER_BUTTON, 97))
            start_token = (await state.get_data())["discover_screen_token"]
            start_message_id = (await state.get_data())["discover_active_message_id"]
            await main_module.dp.feed_update(bot, text_update("/start", 98))
            assert (await state.get_data()).get("discover_screen_token") is None
            assert rendered[-1][0].startswith("Привет, Анна!")
            await main_module.dp.feed_update(bot, callback_update(f"discover:{start_token}:menu", 99,
                                                               message_id=start_message_id))
            assert api.save_calls == ["vacancy-0"]
        finally:
            await state.clear()
            await bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_discover_search_error_and_retry(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:discover-test-token")
        api = FakeApi()
        api.error = httpx.ReadTimeout("source delayed")
        monkeypatch.setattr(main_module, "api_client", api)
        rendered: list[str] = []
        edited_message_ids: list[int] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            rendered.append(text)
            return SimpleNamespace(message_id=100 if len(rendered) == 1 else 200)

        async def edit_text(bot_self: Bot, *, message_id: int, text: str, **kwargs: object) -> None:
            edited_message_ids.append(message_id)
            rendered.append(text)

        async def callback_answer(callback: CallbackQuery, *args: object, **kwargs: object) -> None:
            return None

        async def edit_markup(bot_self: Bot, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_text", edit_text)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_markup)
        monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
        state = FSMContext(storage=main_module.dp.storage, key=StorageKey(bot_id=bot.id, chat_id=456, user_id=123))
        await state.clear()
        try:
            await main_module.dp.feed_update(bot, text_update(DISCOVER_BUTTON, 20))
            await main_module.dp.feed_update(bot, text_update("Python", 21))
            assert "Не удалось получить вакансии" in rendered[-1]
            assert edited_message_ids == [200]
            assert (await state.get_data())["discover_active_message_id"] == 200
            token = (await state.get_data())["discover_screen_token"]
            api.error = None
            await main_module.dp.feed_update(bot, callback_update(f"discover:{token}:retry", 22, message_id=200))
            assert (await state.get_data())["discover_current_screen"] == "list"
            assert api.search_calls == [("Python", 0), ("Python", 0)]
        finally:
            await state.clear()
            await bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_empty_results_can_change_query(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:discover-test-token")
        api = FakeApi()
        api.empty = True
        monkeypatch.setattr(main_module, "api_client", api)
        rendered: list[str] = []
        edited_message_ids: list[int] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            rendered.append(text)
            return SimpleNamespace(message_id=100 if len(rendered) == 1 else 200)

        async def edit_text(bot_self: Bot, *, message_id: int, text: str, **kwargs: object) -> None:
            edited_message_ids.append(message_id)
            rendered.append(text)

        async def callback_answer(callback: CallbackQuery, *args: object, **kwargs: object) -> None:
            return None

        async def edit_markup(bot_self: Bot, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_text", edit_text)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_markup)
        monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
        state = FSMContext(storage=main_module.dp.storage, key=StorageKey(bot_id=bot.id, chat_id=456, user_id=123))
        await state.clear()
        try:
            await main_module.dp.feed_update(bot, text_update(DISCOVER_BUTTON, 300))
            await main_module.dp.feed_update(bot, text_update("unlikely query", 301))
            assert rendered[-1] == "По этому запросу ничего не нашлось."
            assert edited_message_ids == [200]
            assert (await state.get_data())["discover_active_message_id"] == 200
            token = (await state.get_data())["discover_screen_token"]
            await main_module.dp.feed_update(bot, callback_update(f"discover:{token}:query", 302, message_id=200))
            assert await state.get_state() == DiscoverStates.waiting_for_query.state
            assert rendered[-1] == "Что ищем?\nНапример: Python backend"
        finally:
            await state.clear()
            await bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_save_error_preserves_context_and_existing_application_wording(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:discover-test-token")
        api = FakeApi()
        api.application_created = False
        monkeypatch.setattr(main_module, "api_client", api)
        rendered: list[tuple[str, object]] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            rendered.append((text, kwargs.get("reply_markup")))
            return SimpleNamespace(message_id=100)

        async def edit_text(bot_self: Bot, *, text: str, **kwargs: object) -> None:
            rendered.append((text, kwargs.get("reply_markup")))

        async def callback_answer(callback: CallbackQuery, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_text", edit_text)
        monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
        state = FSMContext(storage=main_module.dp.storage, key=StorageKey(bot_id=bot.id, chat_id=456, user_id=123))
        await state.clear()
        try:
            await main_module.dp.feed_update(bot, text_update(DISCOVER_BUTTON, 200))
            await main_module.dp.feed_update(bot, text_update("Python", 201))
            token = (await state.get_data())["discover_screen_token"]
            await main_module.dp.feed_update(bot, callback_update(f"discover:{token}:open:0", 202))
            token = (await state.get_data())["discover_screen_token"]
            request = httpx.Request("POST", "http://api/save")
            response = httpx.Response(404, request=request, json={"detail": {"code": "vacancy_not_found"}})
            api.save_error = httpx.HTTPStatusError("not found", request=request, response=response)
            await main_module.dp.feed_update(bot, callback_update(f"discover:{token}:save", 203))
            assert "вакансия уже недоступна" in rendered[-1][0]
            assert (await state.get_data())["discover_selected_index"] == 0
            retry_token = (await state.get_data())["discover_screen_token"]
            api.save_error = None
            await main_module.dp.feed_update(bot, callback_update(f"discover:{retry_token}:retry_save", 204))
            assert api.save_calls == ["vacancy-0", "vacancy-0"]
            assert "✅ Уже в моих вакансиях" in str(rendered[-1][1])
            assert "Сохранено только что" not in rendered[-1][0]
        finally:
            await state.clear()
            await bot.session.close()
    asyncio.run(scenario())
