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
    PROFILE_API_ERROR_MESSAGE,
    PROFILE_CANCELLED_MESSAGE,
    PROFILE_INVALID_CURRENCY_MESSAGE,
    PROFILE_SAVED_MESSAGE,
    ProfileSetupStates,
    handle_languages,
    handle_location,
    handle_profile_callback,
    handle_profile_cancel,
    handle_profile_setup,
    handle_salary,
    handle_skills,
    handle_target_roles,
    parse_languages,
    parse_salary,
)
from app.menu import ADD_JOB_BUTTON, main_menu_action
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
        self.edited_texts: list[str] = []
        self.next_message_id = 1
        self.fail_edit = False
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

    async def edit_text(self, text: str, reply_markup: object | None = None) -> None:
        if self.fail_edit:
            raise TelegramBadRequest(method=EditMessageReplyMarkup(), message="edit failed")
        assert reply_markup is None
        self.edited_texts.append(text)


class FakeBot:
    def __init__(self) -> None:
        self.removed_keyboards: list[tuple[int, int]] = []
        self.fail_edit = False
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
        self, error: httpx.HTTPError | None = None, profile_error: httpx.HTTPError | None = None
    ) -> None:
        self.error = error
        self.profile_error = profile_error
        self.profile_calls: list[tuple[int, dict[str, object]]] = []
        self.telegram_users: list[object] = []

    async def create_or_get_user(self, telegram_user: object) -> int:
        self.telegram_users.append(telegram_user)
        if self.error is not None:
            raise self.error
        return 7

    async def put_user_profile(self, user_id: int, profile: dict[str, object]) -> dict[str, object]:
        self.profile_calls.append((user_id, profile))
        if self.profile_error is not None:
            raise self.profile_error
        return profile


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
        assert "🎯 Роли: Python Backend Developer, ML Engineer" in summary
        assert "📍 Локации: Yerevan, Tbilisi" in summary
        assert "💰 Минимум: 2500 USD / месяц" in summary

        await handle_profile_callback(FakeCallback("profile:save", message), state, api_client)
        assert await state.get_state() is None
        assert message.answers[-1][0] == PROFILE_SAVED_MESSAGE
        assert message.inline_keyboards_removed == 1
        assert message.edited_texts == [
            "Какие у тебя основные навыки? Например: Python, FastAPI, PostgreSQL\n⏭ Пропущено",
            "Какой у тебя уровень опыта?\n✅ Middle",
            "Какой формат работы предпочитаешь?\n✅ Remote",
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


def test_salary_and_language_parsers() -> None:
    assert parse_salary("2500 usd / month") == {
        "salary_min": "2500", "salary_currency": "USD", "salary_period": "month",
    }
    assert parse_salary("-1 USD / month") is None
    assert parse_languages("English B2, Russian native") == [
        {"language": "English", "level": "B2"}, {"language": "Russian", "level": "native"},
    ]
    assert parse_languages("English") is None
