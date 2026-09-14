import asyncio
import os
from types import SimpleNamespace

import httpx
import pytest
from aiogram import Bot
from aiogram.types import Message, CallbackQuery

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:history-test-token")
import app.main as main
from app.profile import ACTIVE_PROFILE_PROMPT_MESSAGE_ID, PERSISTED_PROFILE_SNAPSHOT, PROFILE_SECTION_MESSAGE_ID, ProfileSetupStates
from app.work_history import payload
from test_main_menu import set_dispatcher_state, make_profile_callback_update, make_update, saved_profile


class Api:
    def __init__(self):
        self.entries = []
        self.writes = []
        self.facts = []
        self.failure = None

    async def create_or_get_user(self, actor):
        return 7

    async def list_work_experiences(self, user):
        return self.entries

    async def save_work_experience(self, user, data, entry_id=None):
        if self.failure:
            raise self.failure
        self.writes.append((dict(data), entry_id))
        entry = {**data, "id": entry_id or len(self.entries) + 1}
        self.entries = [item for item in self.entries if item["id"] != entry["id"]] + [entry]
        return entry

    async def delete_work_experience(self, user, entry_id):
        self.writes.append(("delete", entry_id))
        self.entries = [item for item in self.entries if item["id"] != entry_id]

    async def create_profile_experience_fact(self, user, text):
        self.facts.append(text)
        return {"id": len(self.facts), "text": text}

    async def put_user_profile(self, user, data):
        return data


async def harness(monkeypatch):
    bot = Bot("123456:history-test-token")
    api = Api()
    rendered = []
    async def answer(message, text, **kwargs):
        rendered.append((text, kwargs))
        return SimpleNamespace(message_id=100 + len(rendered))
    async def edit(bot, text, **kwargs):
        rendered.append((text, kwargs))
    async def noop(*args, **kwargs):
        pass
    monkeypatch.setattr(main, "api_client", api)
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(Bot, "edit_message_text", edit)
    monkeypatch.setattr(Message, "edit_reply_markup", noop)
    monkeypatch.setattr(Message, "delete", noop)
    monkeypatch.setattr(CallbackQuery, "answer", noop)
    monkeypatch.setattr(Bot, "edit_message_reply_markup", noop)
    monkeypatch.setattr(Bot, "delete_message", noop)
    state = await set_dispatcher_state(bot, None, {PROFILE_SECTION_MESSAGE_ID: 10, PERSISTED_PROFILE_SNAPSHOT: saved_profile()})
    counter = 1000
    async def click(action, raw=False):
        nonlocal counter
        counter += 1
        data = await state.get_data()
        callback = action if raw else f"history:{data['history_token']}:{action}"
        await main.dp.feed_update(bot, make_profile_callback_update(callback, update_id=counter, message_id=data.get(ACTIVE_PROFILE_PROMPT_MESSAGE_ID, 10)))
    async def text(value):
        nonlocal counter
        counter += 1
        await main.dp.feed_update(bot, make_update(value, update_id=counter, command=value.startswith("/")))
    return bot, state, api, rendered, click, text


def test_dispatcher_manual_precision_confirm_edit_delete_and_stale(monkeypatch):
    async def scenario():
        bot, state, api, rendered, click, text = await harness(monkeypatch)
        await click("profile_section:work_history", raw=True)
        await click("add")
        for value in ("<Acme> &", "Python Developer", "2024", "сейчас"):
            await text(value)
        assert api.writes == []
        assert "2024 → сейчас" in rendered[-1][0]
        assert rendered[-1][1]["parse_mode"] is None
        stale = dict(await state.get_data())
        await click("save")
        assert api.entries[0]["start_month"] is None
        assert api.entries[0]["engagement_kind"] == "unknown"
        await main.dp.feed_update(bot, make_profile_callback_update(f"history:{stale['history_token']}:save", update_id=2000, message_id=stale[ACTIVE_PROFILE_PROMPT_MESSAGE_ID]))
        assert len(api.writes) == 1
        await click("select:0")
        await click("field:position")
        await text("Backend Developer")
        assert len(api.writes) == 1
        await click("save")
        assert api.writes[-1][1] == 1
        await click("select:0")
        await click("delete")
        assert len(api.entries) == 1
        await click("delete_confirm")
        assert api.entries == []
        await bot.session.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("exit_action", ["/cancel", "finish"])
def test_dispatcher_cv_optional_hub_per_entry_confirmation_partial_failure(monkeypatch, exit_action):
    async def scenario():
        bot, state, api, rendered, click, text = await harness(monkeypatch)
        await state.set_state(ProfileSetupStates.summary)
        await state.update_data(**saved_profile(), profile_draft_source="cv_replacement",
            cv_suggested_work_experience=[payload({"company": "Acme"}), payload({"company": "Next"})],
            cv_suggested_experience_facts=["Делал API"], active_profile_prompt_message_id=10)
        await click("profile:save", raw=True)
        assert api.writes == [] and api.facts == []
        assert "Профиль сохранён" in rendered[-1][0]
        await click("category:work")
        await click("select:0")
        await click("field:company")
        await text("Confirmed Acme")
        await click("save")
        assert api.entries[0]["company"] == "Confirmed Acme"
        await click("select:0")
        api.failure = httpx.ReadTimeout("timeout")
        await click("save")
        assert len(api.entries) == 1
        assert (await state.get_data())[PERSISTED_PROFILE_SNAPSHOT]["skills"] == saved_profile()["skills"]
        api.failure = None
        await click("save")
        assert len(api.entries) == 2
        await click("home")
        await click("category:fact")
        await click("select:0")
        await click("field:fact")
        await text("Интегрировал API")
        assert api.facts == []
        await click("save")
        assert api.facts == ["Интегрировал API"]
        await click("home")
        if exit_action == "/cancel":
            await text(exit_action)
        else:
            await click(exit_action)
        assert await state.get_state() is None
        assert "history_token" not in await state.get_data()
        await bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_limit_duplicate_and_cancel(monkeypatch):
    async def scenario():
        bot, state, api, rendered, click, text = await harness(monkeypatch)
        await state.update_data(cv_suggested_work_experience=[payload({"company": "Acme"})])
        # Enter via profile save, exercising the same production routing.
        await state.set_state(ProfileSetupStates.summary)
        await state.update_data(**saved_profile(), active_profile_prompt_message_id=10)
        await click("profile:save", raw=True)
        await click("category:work")
        await click("select:0")
        def error(code):
            response = httpx.Response(422, json={"detail": {"code": code}}, request=httpx.Request("POST", "http://api/work"))
            return httpx.HTTPStatusError("failure", request=response.request, response=response)
        api.failure = error("WORK_EXPERIENCE_LIMIT_REACHED")
        await click("save")
        assert "20" in rendered[-1][0] and api.writes == []
        api.failure = error("DUPLICATE_WORK_EXPERIENCE")
        await click("save")
        assert (await state.get_data())["cv_suggested_work_experience"] == []
        await text("/cancel")
        assert await state.get_state() is None
        await bot.session.close()
    asyncio.run(scenario())


def test_dispatcher_cv_skip_and_main_menu_invalidate_without_writes(monkeypatch):
    async def scenario():
        from test_main_menu import ADD_JOB_BUTTON
        from app.jobs import AddJobStates

        bot, state, api, rendered, click, text = await harness(monkeypatch)
        await state.set_state(ProfileSetupStates.summary)
        await state.update_data(**saved_profile(), active_profile_prompt_message_id=10,
            cv_suggested_work_experience=[payload({"company": "Acme"}), payload({"company": "Other"})])
        await click("profile:save", raw=True)
        await click("category:work")
        await click("select:0")
        await click("skip")
        assert len((await state.get_data())["cv_suggested_work_experience"]) == 1
        await click("select:0")
        stale = dict(await state.get_data())
        await text(ADD_JOB_BUTTON)
        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert "history_token" not in await state.get_data()
        await main.dp.feed_update(bot, make_profile_callback_update(
            f"history:{stale['history_token']}:save", update_id=3000,
            message_id=stale[ACTIVE_PROFILE_PROMPT_MESSAGE_ID]))
        assert api.writes == [] and api.facts == []
        assert await state.get_state() == AddJobStates.waiting_for_url.state
        await bot.session.close()
    asyncio.run(scenario())


def test_work_list_and_unvalidated_draft_fit_telegram_limit(monkeypatch):
    async def scenario():
        bot, state, api, rendered, click, text = await harness(monkeypatch)
        api.entries = [{**payload({"company": "😀" * 200, "position": "<>&_*" + "😀" * 195}), "id": i + 1} for i in range(20)]
        await click("profile_section:work_history", raw=True)
        body, options = rendered[-1]
        assert len(body.encode("utf-16-le")) // 2 <= 4096
        assert "…" in body and "<>&_*" in body
        assert options["parse_mode"] is None
        assert len([row for row in options["reply_markup"].inline_keyboard if ":select:" in row[0].callback_data]) == 20
        await click("select:19")
        assert "😀" * 200 in rendered[-1][0]
        await click("field:company")
        oversized = "😀" * 4000
        await text(oversized)
        assert len(rendered[-1][0].encode("utf-16-le")) // 2 <= 4096
        assert (await state.get_data())["history_draft"]["company"] == oversized
        assert api.writes == []
        await bot.session.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("fallback", [False, True])
def test_dispatcher_canonical_work_history_and_back(monkeypatch, fallback):
    async def scenario():
        from aiogram.exceptions import TelegramBadRequest
        from aiogram.methods import EditMessageText

        bot, state, api, rendered, click, text = await harness(monkeypatch)
        sends, edits, cleaned = [], [], []
        async def answer(message, body, **kwargs):
            sends.append(body)
            return SimpleNamespace(message_id=99)
        async def edit(bot, body, **kwargs):
            edits.append((body, kwargs["message_id"]))
            if fallback and kwargs["message_id"] == 10:
                raise TelegramBadRequest(method=EditMessageText(chat_id=456, message_id=10, text=body), message="cannot edit")
        async def cleanup(bot, **kwargs):
            cleaned.append(kwargs["message_id"])
            if fallback:
                raise TelegramBadRequest(method=EditMessageText(chat_id=456, message_id=10, text="cleanup"), message="cannot clean")
        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_text", edit)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", cleanup)
        await click("profile_section:work_history", raw=True)
        canonical = 99 if fallback else 10
        assert len(sends) == int(fallback)
        assert edits[0] == ("💼 Места работы\n\nПока нет мест работы.", 10)
        assert (await state.get_data())[PROFILE_SECTION_MESSAGE_ID] == canonical
        if fallback:
            assert cleaned == [10]
            previous = len(edits)
            await main.dp.feed_update(bot, make_profile_callback_update(
                "profile_section:work_history", update_id=4000, message_id=10))
            assert len(edits) == previous
        stale = dict(await state.get_data())
        await click("finish")
        assert await state.get_state() is None
        assert (await state.get_data())[PROFILE_SECTION_MESSAGE_ID] == canonical
        assert edits[-1][1] == canonical and "🧩 Навыки" in edits[-1][0]
        assert len(sends) == int(fallback)
        await main.dp.feed_update(bot, make_profile_callback_update(
            f"history:{stale['history_token']}:add", update_id=4001, message_id=canonical))
        assert await state.get_state() is None and api.writes == []
        await bot.session.close()
    asyncio.run(scenario())
