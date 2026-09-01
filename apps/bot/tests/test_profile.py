import asyncio
from types import SimpleNamespace

import httpx
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageReplyMarkup

from app.profile import (
    ACTIVE_PROFILE_PROMPT_MESSAGE_ID,
    INVALID_LANGUAGES_MESSAGE,
    INVALID_SALARY_MESSAGE,
    PERSISTED_PROFILE_SNAPSHOT,
    PROFILE_API_ERROR_MESSAGE,
    PROFILE_CANCELLED_MESSAGE,
    PROFILE_DRAFT_SOURCE,
    PROFILE_EDITING_FIELD,
    PROFILE_INVALID_CURRENCY_MESSAGE,
    PROFILE_SECTION_MESSAGE_ID,
    PROFILE_SAVED_MESSAGE,
    ProfileSetupStates,
    handle_languages,
    handle_location,
    handle_profile_callback,
    handle_profile_cancel,
    handle_profile_draft_field_input,
    handle_profile_setup,
    handle_salary,
    handle_skills,
    handle_target_roles,
    parse_languages,
    parse_salary,
)
from app.menu import ADD_JOB_BUTTON, main_menu_action, profile_section_edit
from app.jobs import AddJobStates, REQUEST_URL_MESSAGE


class FakeMessage:
    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.message_id = 1
        self.from_user = SimpleNamespace(
            id=123, first_name="Анна", last_name=None, username="anna", language_code="ru"
        )
        self.answers: list[tuple[str, object | None]] = []
        self.inline_keyboards_removed = 0
        self.inline_keyboard_removed_message_ids: list[int] = []
        self.deleted_message_ids: list[int] = []
        self.edited_texts: list[str] = []
        self.next_message_id = 1
        self.fail_edit = False
        self.fail_delete = False
        self.chat = SimpleNamespace(id=456)
        self.bot = FakeBot()

    async def answer(self, text: str, reply_markup: object | None = None) -> SimpleNamespace:
        self.answers.append((text, reply_markup))
        prompt = SimpleNamespace(message_id=self.next_message_id)
        self.next_message_id += 1
        self.message_id = prompt.message_id
        return prompt

    async def edit_reply_markup(self, reply_markup: object | None = None) -> None:
        if self.fail_edit:
            raise TelegramBadRequest(method=EditMessageReplyMarkup(), message="edit failed")
        assert reply_markup is None
        self.inline_keyboards_removed += 1
        self.inline_keyboard_removed_message_ids.append(self.message_id)

    async def delete(self) -> None:
        if self.fail_delete:
            raise TelegramBadRequest(method=EditMessageReplyMarkup(), message="delete failed")
        self.deleted_message_ids.append(self.message_id)

    async def edit_text(self, text: str, reply_markup: object | None = None) -> None:
        if self.fail_edit:
            raise TelegramBadRequest(method=EditMessageReplyMarkup(), message="edit failed")
        assert reply_markup is None
        self.edited_texts.append(text)


class FakeBot:
    def __init__(self) -> None:
        self.deleted_messages: list[tuple[int, int]] = []
        self.removed_keyboards: list[tuple[int, int]] = []
        self.edited_messages: list[tuple[int, int, str, object | None]] = []
        self.fail_edit = False
        self.fail_delete = False
        self.fail_text_edit = False
        self.edit_error_message = "edit failed"

    async def edit_message_reply_markup(
        self, *, chat_id: int, message_id: int, reply_markup: object | None = None
    ) -> None:
        if self.fail_edit:
            raise TelegramBadRequest(
                method=EditMessageReplyMarkup(), message=self.edit_error_message
            )
        assert reply_markup is None
        self.removed_keyboards.append((chat_id, message_id))

    async def delete_message(self, *, chat_id: int, message_id: int) -> None:
        self.deleted_messages.append((chat_id, message_id))
        if self.fail_delete:
            raise TelegramBadRequest(
                method=EditMessageReplyMarkup(), message=self.edit_error_message
            )

    async def edit_message_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: object | None = None,
    ) -> None:
        if self.fail_text_edit:
            raise TelegramBadRequest(
                method=EditMessageReplyMarkup(), message=self.edit_error_message
            )
        self.edited_messages.append((chat_id, message_id, text, reply_markup))


class FakeCallback:
    def __init__(self, data: str, message: FakeMessage, *, from_user: object | None = None) -> None:
        self.data = data
        self.message = message
        self.from_user = from_user or SimpleNamespace(
            id=123, first_name="Анна", last_name=None, username="anna", language_code="ru"
        )

    async def answer(self) -> None:
        return None


class FakeApiClient:
    def __init__(
        self,
        error: httpx.HTTPError | None = None,
        profile_error: httpx.HTTPError | None = None,
        returned_profile: dict[str, object] | None = None,
    ) -> None:
        self.error = error
        self.profile_error = profile_error
        self.profile_calls: list[tuple[int, dict[str, object]]] = []
        self.telegram_users: list[object] = []
        self.returned_profile = returned_profile

    async def create_or_get_user(self, telegram_user: object) -> int:
        self.telegram_users.append(telegram_user)
        if self.error is not None:
            raise self.error
        return 7

    async def put_user_profile(self, user_id: int, profile: dict[str, object]) -> dict[str, object]:
        self.profile_calls.append((user_id, profile))
        if self.profile_error is not None:
            raise self.profile_error
        return self.returned_profile or profile


def make_state() -> tuple[MemoryStorage, FSMContext]:
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=1, user_id=123))
    return storage, state


def test_complete_profile_flow_saves_only_after_confirmation_and_clears_state() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage()
        api_client = FakeApiClient()
        await handle_profile_setup(message, state)
        message.text = "Python Backend Developer, ML Engineer"
        await handle_target_roles(message, state)
        await handle_profile_callback(FakeCallback("profile:skip:skills", message), state, api_client)
        await handle_profile_callback(FakeCallback("profile:experience:middle", message), state, api_client)
        message.text = "Yerevan, Tbilisi"
        await handle_location(message, state)
        await handle_profile_callback(FakeCallback("profile:workplace:remote", message), state, api_client)
        message.text = "2500 usd / month"
        await handle_salary(message, state)
        message.text = "English B2, Russian native"
        await handle_languages(message, state)

        assert await state.get_state() == ProfileSetupStates.summary.state
        assert api_client.profile_calls == []
        summary = message.answers[-1][0]
        assert "🎯 Целевые роли: Python Backend Developer, ML Engineer" in summary
        assert "📍 Локация: Yerevan, Tbilisi" in summary
        assert "💰 Зарплата: 2500 USD / месяц" in summary

        await handle_profile_callback(FakeCallback("profile:save", message), state, api_client)
        assert await state.get_state() is None
        assert message.answers[-1][0] == PROFILE_SAVED_MESSAGE
        assert message.inline_keyboards_removed == 1
        assert message.edited_texts == [
            "Какие у тебя основные навыки? Например: Python, FastAPI, PostgreSQL\n⏭ Пропущено",
            "Какой у тебя уровень опыта?\n✅ Middle",
            "Какой формат работы предпочитаешь?\n✅ Удалённо",
        ]
        assert api_client.profile_calls[0][0] == 7
        assert api_client.profile_calls[0][1]["target_roles"] == ["Python Backend Developer", "ML Engineer"]
        assert api_client.profile_calls[0][1]["salary_currency"] == "USD"
        await storage.close()
    asyncio.run(scenario())


def test_minimal_profile_uses_empty_and_default_semantics() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage("Data Engineer")
        api_client = FakeApiClient()
        await handle_profile_setup(message, state)
        await handle_target_roles(message, state)
        await handle_profile_callback(FakeCallback("profile:skip:skills", message), state, api_client)
        await handle_profile_callback(FakeCallback("profile:experience:unknown", message), state, api_client)
        await handle_profile_callback(FakeCallback("profile:skip:location", message), state, api_client)
        await handle_profile_callback(FakeCallback("profile:workplace:any", message), state, api_client)
        await handle_profile_callback(FakeCallback("profile:skip:salary", message), state, api_client)
        await handle_profile_callback(FakeCallback("profile:skip:languages", message), state, api_client)
        await handle_profile_callback(FakeCallback("profile:save", message), state, api_client)
        assert api_client.profile_calls[0][1] == {
            "target_roles": ["Data Engineer"], "skills": [], "experience": "unknown",
            "location": [], "workplace_preference": "any", "salary_min": None,
            "salary_currency": None, "salary_period": "unknown", "languages": [],
        }
        await storage.close()
    asyncio.run(scenario())


def test_temporary_api_error_keeps_summary_draft_for_retry() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        state_data = {
            "target_roles": ["Engineer"], "skills": [], "experience": "unknown", "location": [],
            "workplace_preference": "any", "salary_min": None, "salary_currency": None,
            "salary_period": "unknown", "languages": [],
        }
        await state.set_state(ProfileSetupStates.summary)
        message = FakeMessage()
        await state.set_data({**state_data, ACTIVE_PROFILE_PROMPT_MESSAGE_ID: message.message_id})
        await handle_profile_callback(
            FakeCallback("profile:save", message), state, FakeApiClient(httpx.ReadTimeout("timed out"))
        )
        assert await state.get_state() == ProfileSetupStates.summary.state
        assert await state.get_data() == {**state_data, ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 1}
        assert message.answers[-1][0] == PROFILE_API_ERROR_MESSAGE
        assert message.answers[-1][1] is not None
        assert message.inline_keyboards_removed == 1

        successful_client = FakeApiClient()
        await handle_profile_callback(FakeCallback("profile:save", message), state, successful_client)
        assert await state.get_state() is None
        assert len(successful_client.profile_calls) == 1
        await storage.close()
    asyncio.run(scenario())


def test_invalid_iso_currency_keeps_summary_and_explains_error() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.summary)
        await state.set_data(
            {
                "target_roles": ["Engineer"],
                "salary_min": "2500",
                "salary_currency": "ABC",
                "salary_period": "month",
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 1,
            }
        )
        message = FakeMessage()
        request = httpx.Request("PUT", "http://api/users/7/profile")
        response = httpx.Response(
            422,
            request=request,
            json={
                "detail": [
                    {
                        "loc": ["body", "salary_currency"],
                        "msg": "Value error, salary_currency must be an active ISO 4217 code",
                    }
                ]
            },
        )
        api_client = FakeApiClient(
            profile_error=httpx.HTTPStatusError("validation error", request=request, response=response)
        )

        await handle_profile_callback(FakeCallback("profile:save", message), state, api_client)

        assert await state.get_state() == ProfileSetupStates.summary.state
        assert message.answers[-1][0] == PROFILE_INVALID_CURRENCY_MESSAGE
        assert message.inline_keyboards_removed == 1
        await storage.close()

    asyncio.run(scenario())


def test_retry_summary_keyboard_is_removed_by_main_menu_navigation() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.summary)
        message = FakeMessage()
        await state.set_data(
            {"target_roles": ["Engineer"], ACTIVE_PROFILE_PROMPT_MESSAGE_ID: message.message_id}
        )

        await handle_profile_callback(
            FakeCallback("profile:save", message), state, FakeApiClient(httpx.ReadTimeout("timed out"))
        )
        retry_message_id = (await state.get_data())[ACTIVE_PROFILE_PROMPT_MESSAGE_ID]

        message.text = ADD_JOB_BUTTON
        await main_menu_action(message, state)

        assert message.bot.removed_keyboards == [(456, retry_message_id)]
        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert message.answers[-1][0] == REQUEST_URL_MESSAGE
        await storage.close()

    asyncio.run(scenario())


def test_retry_cleanup_errors_do_not_block_profile_error_or_menu_navigation() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.summary)
        message = FakeMessage()
        await state.set_data(
            {"target_roles": ["Engineer"], ACTIVE_PROFILE_PROMPT_MESSAGE_ID: message.message_id}
        )
        message.fail_edit = True

        await handle_profile_callback(
            FakeCallback("profile:save", message), state, FakeApiClient(httpx.ReadTimeout("timed out"))
        )

        assert await state.get_state() == ProfileSetupStates.summary.state
        assert message.answers[-1][0] == PROFILE_API_ERROR_MESSAGE

        message.bot.fail_edit = True
        message.text = ADD_JOB_BUTTON
        await main_menu_action(message, state)

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert message.answers[-1][0] == REQUEST_URL_MESSAGE
        await storage.close()

    asyncio.run(scenario())


def test_valid_free_text_removes_skip_keyboard() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage("Engineer")
        await handle_profile_setup(message, state)
        await handle_target_roles(message, state)
        skills_prompt_id = (await state.get_data())["skip_prompt_message_id"]

        message.text = "Python, FastAPI"
        await handle_skills(message, state)

        assert message.bot.removed_keyboards == [(456, skills_prompt_id)]
        assert await state.get_state() == ProfileSetupStates.experience.state
        await storage.close()

    asyncio.run(scenario())


def test_skip_marks_prompt_as_skipped() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage()
        await state.set_state(ProfileSetupStates.salary)
        await state.update_data(
            **{ACTIVE_PROFILE_PROMPT_MESSAGE_ID: message.message_id}
        )

        await handle_profile_callback(FakeCallback("profile:skip:salary", message), state, FakeApiClient())

        assert message.edited_texts == [
            "Какая минимальная зарплата тебе интересна? Например: 2500 USD / month\n⏭ Пропущено"
        ]
        assert await state.get_state() == ProfileSetupStates.languages.state
        await storage.close()

    asyncio.run(scenario())


def test_save_uses_callback_user_not_bot_summary_message_author() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.summary)
        bot_summary_message = FakeMessage()
        await state.set_data(
            {
                "target_roles": ["Engineer"],
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: bot_summary_message.message_id,
            }
        )
        bot_summary_message.from_user = SimpleNamespace(id=999_999, first_name="Job Hunter Bot")
        callback_user = SimpleNamespace(
            id=123, first_name="Анна", last_name=None, username="anna", language_code="ru"
        )
        api_client = FakeApiClient()

        await handle_profile_callback(
            FakeCallback("profile:save", bot_summary_message, from_user=callback_user), state, api_client
        )

        assert api_client.telegram_users == [callback_user]
        assert api_client.telegram_users[0].id == 123
        assert api_client.telegram_users[0].id != bot_summary_message.from_user.id
        await storage.close()

    asyncio.run(scenario())


def test_cancel_clears_profile_state() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.salary)
        await state.update_data(target_roles=["Engineer"])
        message = FakeMessage()
        await handle_profile_cancel(message, state)
        assert await state.get_state() is None
        assert await state.get_data() == {}
        assert message.answers[-1][0] == PROFILE_CANCELLED_MESSAGE
        await storage.close()
    asyncio.run(scenario())


def test_cancel_callback_removes_inline_keyboard() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.summary)
        message = FakeMessage()
        await state.update_data(
            **{ACTIVE_PROFILE_PROMPT_MESSAGE_ID: message.message_id}
        )

        await handle_profile_callback(FakeCallback("profile:cancel", message), state, FakeApiClient())

        assert await state.get_state() is None
        assert message.inline_keyboards_removed == 1
        await storage.close()

    asyncio.run(scenario())


def test_persisted_save_sends_new_authoritative_card_and_deactivates_old_context() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        original = {
            "target_roles": ["Engineer"],
            "skills": ["Python"],
            "experience": "middle",
            "location": ["Yerevan"],
            "workplace_preference": "remote",
            "salary_min": "2500",
            "salary_currency": "USD",
            "salary_period": "month",
            "languages": [{"language": "English", "level": "B2"}],
        }
        draft = {**original, "skills": ["Python", "FastAPI"]}
        authoritative = {**draft, "skills": ["Python", "FastAPI", "SQL"]}
        await state.set_state(ProfileSetupStates.summary)
        await state.set_data(
            {
                **draft,
                PROFILE_DRAFT_SOURCE: "persisted",
                PERSISTED_PROFILE_SNAPSHOT: original,
                PROFILE_SECTION_MESSAGE_ID: 10,
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 11,
                "unrelated_fsm_metadata": "must not leak",
            }
        )
        message = FakeMessage()
        message.message_id = 11
        message.next_message_id = 12
        api = FakeApiClient(returned_profile=authoritative)
        callback = FakeCallback("profile:save", message)

        await handle_profile_callback(callback, state, api)

        assert api.profile_calls == [(7, draft)]
        assert await state.get_state() is None
        state_data = await state.get_data()
        assert state_data[PERSISTED_PROFILE_SNAPSHOT] == authoritative
        assert state_data[PROFILE_SECTION_MESSAGE_ID] == 12
        assert message.deleted_message_ids == [11]
        assert message.inline_keyboard_removed_message_ids == []
        assert message.bot.deleted_messages == [(456, 10)]
        assert message.bot.removed_keyboards == []
        assert len(message.answers) == 1
        text, keyboard = message.answers[0]
        assert "🧩 Навыки: Python, FastAPI, SQL" in text
        assert keyboard.inline_keyboard[0][0].callback_data == "profile_section:edit"

        old_card = FakeMessage()
        old_card.message_id = 10
        old_card.bot = message.bot
        await profile_section_edit(FakeCallback("profile_section:edit", old_card), state)

        assert await state.get_state() is None
        assert old_card.answers == []

        section_message = FakeMessage()
        section_message.message_id = 12
        section_message.bot = message.bot
        await profile_section_edit(FakeCallback("profile_section:edit", section_message), state)

        assert await state.get_state() == ProfileSetupStates.edit_field.state
        assert section_message.answers[-1][0] == "Какое поле изменить?"
        assert message.bot.removed_keyboards == [(456, 12)]
        await storage.close()

    asyncio.run(scenario())


def test_persisted_save_continues_when_old_card_cleanup_fails() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        profile = {
            "target_roles": ["Engineer"],
            "skills": ["Python"],
            "experience": "middle",
            "location": ["Yerevan"],
            "workplace_preference": "remote",
            "salary_min": "2500",
            "salary_currency": "USD",
            "salary_period": "month",
            "languages": [],
        }
        await state.set_state(ProfileSetupStates.summary)
        await state.set_data(
            {
                **profile,
                PROFILE_DRAFT_SOURCE: "persisted",
                PERSISTED_PROFILE_SNAPSHOT: profile,
                PROFILE_SECTION_MESSAGE_ID: 10,
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 11,
            }
        )
        message = FakeMessage()
        message.message_id = 11
        message.next_message_id = 12
        message.bot.fail_delete = True

        await handle_profile_callback(
            FakeCallback("profile:save", message), state, FakeApiClient(returned_profile=profile)
        )

        assert message.deleted_message_ids == [11]
        assert message.inline_keyboard_removed_message_ids == []
        assert message.bot.deleted_messages == [(456, 10)]
        assert message.bot.removed_keyboards == [(456, 10)]
        assert len(message.answers) == 1
        assert message.answers[0][1].inline_keyboard[0][0].callback_data == "profile_section:edit"
        assert (await state.get_data())[PROFILE_SECTION_MESSAGE_ID] == 12
        await storage.close()

    asyncio.run(scenario())


def test_persisted_cancel_discards_draft_and_restores_original_card_without_put() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        original = {
            "target_roles": ["Engineer"],
            "skills": ["Python"],
            "experience": "middle",
            "location": [],
            "workplace_preference": "any",
            "salary_min": None,
            "salary_currency": None,
            "salary_period": "unknown",
            "languages": [],
        }
        await state.set_state(ProfileSetupStates.summary)
        await state.set_data(
            {
                **original,
                "skills": ["Changed draft"],
                PROFILE_DRAFT_SOURCE: "persisted",
                PERSISTED_PROFILE_SNAPSHOT: original,
                PROFILE_SECTION_MESSAGE_ID: 10,
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 11,
            }
        )
        message = FakeMessage()
        message.message_id = 11
        message.next_message_id = 12
        api = FakeApiClient()

        await handle_profile_callback(FakeCallback("profile:cancel", message), state, api)

        assert api.profile_calls == []
        assert await state.get_state() is None
        assert "🧩 Навыки: Python" in message.answers[-1][0]
        assert "Changed draft" not in message.answers[-1][0]
        assert (await state.get_data())[PERSISTED_PROFILE_SNAPSHOT] == original
        assert (await state.get_data())[PROFILE_SECTION_MESSAGE_ID] == 12
        assert message.deleted_message_ids == [11]
        assert message.inline_keyboard_removed_message_ids == []
        assert message.bot.deleted_messages == [(456, 10)]
        await storage.close()

    asyncio.run(scenario())


def test_persisted_put_failure_keeps_draft_and_retry_actions() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        draft = {
            "target_roles": ["Engineer"],
            "skills": ["Draft skill"],
            "experience": "unknown",
            "location": [],
            "workplace_preference": "any",
            "salary_min": None,
            "salary_currency": None,
            "salary_period": "unknown",
            "languages": [],
        }
        await state.set_state(ProfileSetupStates.summary)
        await state.set_data(
            {
                **draft,
                PROFILE_DRAFT_SOURCE: "persisted",
                PERSISTED_PROFILE_SNAPSHOT: {**draft, "skills": ["Original skill"]},
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 1,
            }
        )
        message = FakeMessage()
        api = FakeApiClient(profile_error=httpx.ReadTimeout("timed out"))

        await handle_profile_callback(FakeCallback("profile:save", message), state, api)

        assert await state.get_state() == ProfileSetupStates.summary.state
        assert (await state.get_data())["skills"] == ["Draft skill"]
        assert api.profile_calls == [(7, draft)]
        assert [row[0].text for row in message.answers[-1][1].inline_keyboard] == [
            "✅ Сохранить",
            "✏️ Изменить",
            "❌ Отменить",
        ]
        await storage.close()

    asyncio.run(scenario())


def test_invalid_optional_input_keeps_skip_keyboard() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage("not a salary")
        await state.set_state(ProfileSetupStates.salary)
        await state.update_data(skip_prompt_message_id=12)

        await handle_salary(message, state)

        assert message.bot.removed_keyboards == []
        assert await state.get_state() == ProfileSetupStates.salary.state
        await storage.close()

    asyncio.run(scenario())


def test_cleanup_failure_does_not_block_transition() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage()
        message.fail_edit = True
        await state.set_state(ProfileSetupStates.experience)
        await state.update_data(
            **{ACTIVE_PROFILE_PROMPT_MESSAGE_ID: message.message_id}
        )

        await handle_profile_callback(FakeCallback("profile:experience:senior", message), state, FakeApiClient())

        assert await state.get_state() == ProfileSetupStates.location.state
        assert (await state.get_data())["experience"] == "senior"
        await storage.close()

    asyncio.run(scenario())


def test_message_not_modified_cleanup_is_idempotent_without_warning(caplog) -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage("Python, FastAPI")
        message.bot.fail_edit = True
        message.bot.edit_error_message = (
            "message is not modified: specified new message content and reply markup "
            "are exactly the same as a current content and reply markup of the message"
        )
        await state.set_state(ProfileSetupStates.skills)
        await state.update_data(
            target_roles=["Engineer"],
            **{ACTIVE_PROFILE_PROMPT_MESSAGE_ID: message.message_id},
        )

        with caplog.at_level("WARNING", logger="app.profile"):
            await handle_skills(message, state)

        assert await state.get_state() == ProfileSetupStates.experience.state
        assert (await state.get_data())["skills"] == ["Python", "FastAPI"]
        assert not any(
            "Could not remove active profile inline keyboard" in record.getMessage()
            for record in caplog.records
        )
        await storage.close()

    asyncio.run(scenario())


def test_other_bad_request_cleanup_logs_warning_and_keeps_transition(caplog) -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage("Python, FastAPI")
        message.bot.fail_edit = True
        await state.set_state(ProfileSetupStates.skills)
        await state.update_data(
            target_roles=["Engineer"],
            **{ACTIVE_PROFILE_PROMPT_MESSAGE_ID: message.message_id},
        )

        with caplog.at_level("WARNING", logger="app.profile"):
            await handle_skills(message, state)

        assert await state.get_state() == ProfileSetupStates.experience.state
        assert (await state.get_data())["skills"] == ["Python", "FastAPI"]
        assert any(
            "Could not remove active profile inline keyboard" in record.getMessage()
            for record in caplog.records
        )
        await storage.close()

    asyncio.run(scenario())


def test_skip_keyboard_removal_failure_does_not_block_text_transition() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage("Python")
        message.bot.fail_edit = True
        await state.set_state(ProfileSetupStates.skills)
        await state.update_data(skip_prompt_message_id=12)

        await handle_skills(message, state)

        assert await state.get_state() == ProfileSetupStates.experience.state
        assert (await state.get_data())["skills"] == ["Python"]
        await storage.close()

    asyncio.run(scenario())


def test_invalid_inputs_keep_current_step() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage("0 USD / month")
        await state.set_state(ProfileSetupStates.salary)
        await handle_salary(message, state)
        assert await state.get_state() == ProfileSetupStates.salary.state
        assert message.answers[-1][0] == INVALID_SALARY_MESSAGE
        message.text = "English"
        await state.set_state(ProfileSetupStates.languages)
        await handle_languages(message, state)
        assert await state.get_state() == ProfileSetupStates.languages.state
        assert message.answers[-1][0] == INVALID_LANGUAGES_MESSAGE
        await storage.close()
    asyncio.run(scenario())


def test_remote_is_not_accepted_as_location() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        message = FakeMessage("Remote")
        await state.set_state(ProfileSetupStates.location)
        await handle_location(message, state)
        assert await state.get_state() == ProfileSetupStates.location.state
        assert "географические" in message.answers[-1][0]
        await storage.close()
    asyncio.run(scenario())


def test_localized_workplace_labels_are_not_accepted_as_locations() -> None:
    async def scenario() -> None:
        invalid_values = (
            "any",
            "remote",
            "hybrid",
            "onsite",
            "Удалённо",
            "удаленно",
            "  УДАЛЕННО  ",
            "Гибрид",
            "На месте работодателя",
            "На   месте работодателя",
            "Любой",
        )
        for value in invalid_values:
            storage, state = make_state()
            message = FakeMessage(value)
            await state.set_state(ProfileSetupStates.location)
            await state.set_data({"location": ["Yerevan"]})

            await handle_location(message, state)

            assert await state.get_state() == ProfileSetupStates.location.state
            assert (await state.get_data())["location"] == ["Yerevan"]
            assert "географические" in message.answers[-1][0]
            await storage.close()

            for source in ("persisted", "cv"):
                storage, state = make_state()
                message = FakeMessage(value)
                await state.set_state(ProfileSetupStates.edit_field)
                await state.set_data(
                    {
                        "target_roles": ["Engineer"],
                        "location": ["Yerevan"],
                        PROFILE_DRAFT_SOURCE: source,
                        PROFILE_EDITING_FIELD: "location",
                    }
                )

                await handle_profile_draft_field_input(message, state)

                assert await state.get_state() == ProfileSetupStates.edit_field.state
                assert (await state.get_data())["location"] == ["Yerevan"]
                assert "географические" in message.answers[-1][0]
                await storage.close()

        for value in ("Россия", "Москва", "Удаленный район"):
            storage, state = make_state()
            message = FakeMessage(value)
            await state.set_state(ProfileSetupStates.location)
            await state.set_data({"location": ["Yerevan"]})

            await handle_location(message, state)

            assert await state.get_state() == ProfileSetupStates.workplace_preference.state
            assert (await state.get_data())["location"] == [value]
            await storage.close()

            for source in ("persisted", "cv"):
                storage, state = make_state()
                message = FakeMessage(value)
                await state.set_state(ProfileSetupStates.edit_field)
                await state.set_data(
                    {
                        "target_roles": ["Engineer"],
                        "location": ["Yerevan"],
                        PROFILE_DRAFT_SOURCE: source,
                        PROFILE_EDITING_FIELD: "location",
                    }
                )

                await handle_profile_draft_field_input(message, state)

                assert await state.get_state() == ProfileSetupStates.summary.state
                assert (await state.get_data())["location"] == [value]
                await storage.close()

    asyncio.run(scenario())


def test_salary_and_language_parsers() -> None:
    assert parse_salary("2500 usd / month") == {
        "salary_min": "2500", "salary_currency": "USD", "salary_period": "month",
    }
    assert parse_salary("-1 USD / month") is None
    assert parse_languages("English B2, Russian native") == [
        {"language": "English", "level": "B2"}, {"language": "Russian", "level": "native"},
    ]
    assert parse_languages("English") is None
