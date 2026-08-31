from __future__ import annotations

import asyncio
import os
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace

import httpx
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import DeleteMessage
from aiogram.types import Chat, Document, Message, MessageEntity, Update, User

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:cv-test-token")

import app.main as main_module
import app.cv_profile as cv_profile_module
from app.cv_profile import (
    CV_API_ERROR_MESSAGE,
    CV_PROCESSING_MESSAGE,
    CV_UNSUPPORTED_MESSAGE,
    CV_UPLOAD_PROMPT,
    handle_cv_document,
    handle_unsupported_cv_message,
)
from app.main import dp
from app.menu import (
    ADD_JOB_BUTTON,
    PROFILE_CV_CALLBACK,
    PROFILE_SECTION_MESSAGE_ID,
    main_menu_action,
    profile_section_cv,
    profile_section_keyboard,
)
from app.profile import (
    ACTIVE_PROFILE_PROMPT_MESSAGE_ID,
    CV_EDITING_FIELD,
    PROFILE_CANCELLED_MESSAGE,
    CV_DRAFT_CANCELLED_MESSAGE,
    PROFILE_DRAFT_SOURCE,
    PROFILE_SAVED_MESSAGE,
    ROLE_PROMPT,
    ProfileSetupStates,
    handle_profile_callback,
    handle_cv_draft_field_input,
)
from app.jobs import AddJobStates, REQUEST_URL_MESSAGE


def draft() -> dict[str, object]:
    return {
        "target_roles": ["Python Engineer"],
        "skills": ["Python", "FastAPI"],
        "experience": "middle",
        "location": ["Yerevan"],
        "workplace_preference": "any",
        "salary_min": None,
        "salary_currency": None,
        "salary_period": "unknown",
        "languages": [{"language": "English", "level": "B2"}],
    }


class FakeBot:
    def __init__(self, content: bytes = b"%PDF-resume") -> None:
        self.content = content
        self.actions: list[tuple[int, object]] = []
        self.removed_keyboards: list[tuple[int, int]] = []

    async def send_chat_action(self, *, chat_id: int, action: object) -> None:
        self.actions.append((chat_id, action))

    async def download(self, document: object, *, destination: BytesIO) -> BytesIO:
        destination.write(self.content)
        destination.seek(0)
        return destination

    async def edit_message_reply_markup(
        self, *, chat_id: int, message_id: int, reply_markup: object | None = None
    ) -> None:
        assert reply_markup is None
        self.removed_keyboards.append((chat_id, message_id))


class FakeMessage:
    def __init__(
        self,
        *,
        document: object | None = None,
        bot: FakeBot | None = None,
        text: str | None = None,
        message_id: int = 10,
    ) -> None:
        self.document = document
        self.bot = bot or FakeBot()
        self.text = text
        self.message_id = message_id
        self.chat = SimpleNamespace(id=456)
        self.from_user = SimpleNamespace(
            id=123, first_name="Anna", last_name=None, username="anna", language_code="en"
        )
        self.answers: list[tuple[str, object | None, int]] = []
        self.edited = 0
        self.edited_texts: list[str] = []
        self.deleted = 0
        self.delete_fails = False

    async def answer(self, text: str, reply_markup: object | None = None) -> SimpleNamespace:
        response_id = 100 + len(self.answers)
        self.answers.append((text, reply_markup, response_id))
        return SimpleNamespace(message_id=response_id)

    async def edit_reply_markup(self, reply_markup: object | None = None) -> None:
        assert reply_markup is None
        self.edited += 1

    async def edit_text(self, text: str, reply_markup: object | None = None) -> None:
        assert reply_markup is None
        self.edited_texts.append(text)

    async def delete(self) -> None:
        if self.delete_fails:
            raise TelegramBadRequest(
                method=DeleteMessage(chat_id=self.chat.id, message_id=self.message_id),
                message="delete failed",
            )
        self.deleted += 1


class FakeCallback:
    def __init__(self, data: str, message: FakeMessage) -> None:
        self.data = data
        self.message = message
        self.from_user = message.from_user
        self.answered = False

    async def answer(self) -> None:
        self.answered = True


class FakeApiClient:
    def __init__(self, *, error: httpx.HTTPError | None = None) -> None:
        self.error = error
        self.draft_calls: list[tuple[int, str, str, bytes]] = []
        self.put_calls: list[tuple[int, dict[str, object]]] = []

    async def create_or_get_user(self, telegram_user: object) -> int:
        return 7

    async def create_profile_draft_from_cv(
        self, user_id: int, *, filename: str, content_type: str, content: bytes
    ) -> dict[str, object]:
        self.draft_calls.append((user_id, filename, content_type, content))
        if self.error is not None:
            raise self.error
        return draft()

    async def put_user_profile(self, user_id: int, profile: dict[str, object]) -> dict[str, object]:
        self.put_calls.append((user_id, profile))
        return profile


def pdf_document(*, size: int = 100) -> SimpleNamespace:
    return SimpleNamespace(
        file_name="resume.pdf", mime_type="application/pdf", file_size=size, file_id="file-id"
    )


def make_state() -> tuple[MemoryStorage, FSMContext]:
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=456, user_id=123))
    return storage, state


def test_profile_section_has_cv_button_and_starts_upload_flow() -> None:
    async def scenario() -> None:
        keyboard = profile_section_keyboard()
        assert keyboard.inline_keyboard[1][0].text == "📄 Заполнить из CV"
        assert keyboard.inline_keyboard[1][0].callback_data == PROFILE_CV_CALLBACK

        storage, state = make_state()
        message = FakeMessage(message_id=12)
        await state.update_data(**{PROFILE_SECTION_MESSAGE_ID: 12})
        callback = FakeCallback(PROFILE_CV_CALLBACK, message)

        await profile_section_cv(callback, state)

        assert callback.answered is True
        assert message.bot.removed_keyboards == [(456, 12)]
        assert await state.get_state() == ProfileSetupStates.cv_waiting_document.state
        assert message.answers[-1][0] == CV_UPLOAD_PROMPT
        await storage.close()

    asyncio.run(scenario())


def test_valid_document_shows_processing_and_draft_summary() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.cv_waiting_document)
        message = FakeMessage(document=pdf_document())
        api = FakeApiClient()

        await handle_cv_document(message, state, api)

        assert message.answers[0][0] == CV_PROCESSING_MESSAGE
        assert message.bot.actions
        assert api.draft_calls == [(7, "resume.pdf", "application/pdf", b"%PDF-resume")]
        assert await state.get_state() == ProfileSetupStates.summary.state
        data = await state.get_data()
        assert data["target_roles"] == ["Python Engineer"]
        assert data[PROFILE_DRAFT_SOURCE] == "cv"
        summary_text, keyboard, summary_id = message.answers[-1]
        assert "🎯 Роли: Python Engineer" in summary_text
        assert [row[0].text for row in keyboard.inline_keyboard] == [
            "✅ Сохранить",
            "✏️ Изменить вручную",
            "❌ Отменить",
        ]
        assert data[ACTIVE_PROFILE_PROMPT_MESSAGE_ID] == summary_id
        await storage.close()

    asyncio.run(scenario())


def test_unsupported_attachment_stays_recoverable() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.cv_waiting_document)
        message = FakeMessage(
            document=SimpleNamespace(
                file_name="resume.txt", mime_type="text/plain", file_size=10, file_id="id"
            )
        )
        api = FakeApiClient()

        await handle_cv_document(message, state, api)
        await handle_unsupported_cv_message(FakeMessage(text="not a document"), state)

        assert await state.get_state() == ProfileSetupStates.cv_waiting_document.state
        assert message.answers[-1][0] == CV_UNSUPPORTED_MESSAGE
        assert api.draft_calls == []
        await storage.close()

    asyncio.run(scenario())


def test_save_calls_put_only_after_explicit_confirmation() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.cv_waiting_document)
        upload = FakeMessage(document=pdf_document())
        api = FakeApiClient()
        await handle_cv_document(upload, state, api)
        assert api.put_calls == []

        summary_id = (await state.get_data())[ACTIVE_PROFILE_PROMPT_MESSAGE_ID]
        callback_message = FakeMessage(message_id=summary_id)
        await handle_profile_callback(FakeCallback("profile:save", callback_message), state, api)

        assert len(api.put_calls) == 1
        assert api.put_calls[0][1]["target_roles"] == ["Python Engineer"]
        assert await state.get_state() is None
        assert callback_message.answers[-1][0] == PROFILE_SAVED_MESSAGE
        await storage.close()

    asyncio.run(scenario())


def test_edit_manual_selects_field_without_starting_sequential_wizard() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.summary)
        await state.set_data(
            {
                **draft(),
                PROFILE_DRAFT_SOURCE: "cv",
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 77,
            }
        )
        message = FakeMessage(message_id=77)
        api = FakeApiClient()

        await handle_profile_callback(FakeCallback("profile:edit_manual", message), state, api)

        assert await state.get_state() == ProfileSetupStates.cv_edit_field.state
        data = await state.get_data()
        assert data["skills"] == ["Python", "FastAPI"]
        assert data["experience"] == "middle"
        assert data["languages"] == [{"language": "English", "level": "B2"}]
        assert message.edited == 1
        assert message.answers[-1][0] == "Какое поле изменить?"
        assert [row[0].text for row in message.answers[-1][1].inline_keyboard] == [
            "🎯 Роли",
            "🧩 Навыки",
            "📈 Опыт",
            "📍 Локации",
            "🏠 Формат работы",
            "💰 Зарплата",
            "🌍 Языки",
        ]
        assert data[ACTIVE_PROFILE_PROMPT_MESSAGE_ID] == 100
        assert api.put_calls == []
        await storage.close()

    asyncio.run(scenario())


def test_cv_field_edit_shows_current_value_and_keeps_draft_until_valid_input() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.cv_edit_field)
        await state.set_data(
            {
                **draft(),
                PROFILE_DRAFT_SOURCE: "cv",
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 71,
            }
        )
        picker = FakeMessage(message_id=71)

        await handle_profile_callback(FakeCallback("profile:edit:skills", picker), state, FakeApiClient())

        data = await state.get_data()
        assert data[CV_EDITING_FIELD] == "skills"
        assert data["skills"] == ["Python", "FastAPI"]
        prompt, keyboard, prompt_id = picker.answers[-1]
        assert prompt == "Текущие навыки:\nPython, FastAPI\n\nОтправь обновлённый список."
        assert keyboard.inline_keyboard[0][0].text == "🗑 Очистить"

        invalid = FakeMessage(text=" , ", message_id=72)
        await handle_cv_draft_field_input(invalid, state)
        assert (await state.get_data())["skills"] == ["Python", "FastAPI"]
        assert await state.get_state() == ProfileSetupStates.cv_edit_field.state

        valid = FakeMessage(text="Python, Django", message_id=73)
        await handle_cv_draft_field_input(valid, state)
        assert (await state.get_data())["skills"] == ["Python", "Django"]
        assert (await state.get_data()).get(CV_EDITING_FIELD) is None
        assert await state.get_state() == ProfileSetupStates.summary.state
        assert "🧩 Навыки: Python, Django" in valid.answers[-1][0]
        await storage.close()

    asyncio.run(scenario())


def test_cv_field_edit_clear_and_enum_replace_only_selected_field() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.cv_edit_field)
        await state.set_data(
            {
                **draft(),
                "salary_min": "2500",
                "salary_currency": "USD",
                "salary_period": "month",
                PROFILE_DRAFT_SOURCE: "cv",
                CV_EDITING_FIELD: "salary",
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 81,
            }
        )
        salary = FakeMessage(message_id=81)
        await handle_profile_callback(FakeCallback("profile:edit_clear:salary", salary), state, FakeApiClient())
        data = await state.get_data()
        assert data["salary_min"] is None
        assert data["salary_currency"] is None
        assert data["salary_period"] == "unknown"
        assert data["skills"] == ["Python", "FastAPI"]
        assert await state.get_state() == ProfileSetupStates.summary.state

        summary_id = data[ACTIVE_PROFILE_PROMPT_MESSAGE_ID]
        summary = FakeMessage(message_id=summary_id)
        await handle_profile_callback(FakeCallback("profile:edit_manual", summary), state, FakeApiClient())
        picker_id = (await state.get_data())[ACTIVE_PROFILE_PROMPT_MESSAGE_ID]
        picker = FakeMessage(message_id=picker_id)
        await handle_profile_callback(FakeCallback("profile:edit:experience", picker), state, FakeApiClient())
        prompt, keyboard, _ = picker.answers[-1]
        assert prompt == "Текущий опыт: Middle\n\nВыбери новое значение."
        assert [row[0].text for row in keyboard.inline_keyboard] == [
            "Intern", "Junior", "Middle", "Senior", "Lead", "Не указывать"
        ]

        enum_id = (await state.get_data())[ACTIVE_PROFILE_PROMPT_MESSAGE_ID]
        enum_message = FakeMessage(message_id=enum_id)
        await handle_profile_callback(
            FakeCallback("profile:edit_enum:experience:junior", enum_message), state, FakeApiClient()
        )
        data = await state.get_data()
        assert data["experience"] == "junior"
        assert data["skills"] == ["Python", "FastAPI"]
        assert await state.get_state() == ProfileSetupStates.summary.state
        await storage.close()

    asyncio.run(scenario())


def test_cv_target_roles_edit_has_no_clear_action() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.cv_edit_field)
        await state.set_data(
            {
                **draft(),
                PROFILE_DRAFT_SOURCE: "cv",
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 91,
            }
        )
        picker = FakeMessage(message_id=91)
        await handle_profile_callback(
            FakeCallback("profile:edit:target_roles", picker), state, FakeApiClient()
        )
        prompt, keyboard, _ = picker.answers[-1]
        assert prompt == "Текущие роли:\nPython Engineer\n\nОтправь обновлённый список."
        assert keyboard is None

        invalid = FakeMessage(text="", message_id=92)
        await handle_cv_draft_field_input(invalid, state)
        assert (await state.get_data())["target_roles"] == ["Python Engineer"]
        await storage.close()

    asyncio.run(scenario())


def test_cancel_clears_draft_without_persistence() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.summary)
        await state.set_data(
            {**draft(), PROFILE_DRAFT_SOURCE: "cv", ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 88}
        )
        message = FakeMessage(message_id=88)
        api = FakeApiClient()

        await handle_profile_callback(FakeCallback("profile:cancel", message), state, api)

        assert await state.get_state() is None
        assert await state.get_data() == {}
        assert api.put_calls == []
        assert message.deleted == 1
        assert message.answers[-1][0] == CV_DRAFT_CANCELLED_MESSAGE
        await storage.close()

    asyncio.run(scenario())


def test_cv_draft_cancel_delete_failure_falls_back_to_keyboard_cleanup() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.summary)
        await state.set_data(
            {**draft(), PROFILE_DRAFT_SOURCE: "cv", ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 89}
        )
        message = FakeMessage(message_id=89)
        message.delete_fails = True

        await handle_profile_callback(
            FakeCallback("profile:cancel", message), state, FakeApiClient()
        )

        assert message.deleted == 0
        assert message.edited == 1
        assert await state.get_state() is None
        assert await state.get_data() == {}
        assert message.answers == [(CV_DRAFT_CANCELLED_MESSAGE, None, 100)]
        await storage.close()

    asyncio.run(scenario())


def test_stale_cv_draft_cancel_after_completion_does_nothing() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.summary)
        await state.set_data(
            {**draft(), PROFILE_DRAFT_SOURCE: "cv", ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 90}
        )
        message = FakeMessage(message_id=90)
        callback = FakeCallback("profile:cancel", message)

        await handle_profile_callback(callback, state, FakeApiClient())
        await handle_profile_callback(callback, state, FakeApiClient())

        assert message.deleted == 1
        assert message.answers == [(CV_DRAFT_CANCELLED_MESSAGE, None, 100)]
        assert await state.get_state() is None
        await storage.close()

    asyncio.run(scenario())


def test_api_failure_returns_to_waiting_state() -> None:
    async def scenario() -> None:
        request = httpx.Request("POST", "http://api/users/7/profile/draft-from-cv")
        response = httpx.Response(504, json={"detail": "ai_timeout"}, request=request)
        error = httpx.HTTPStatusError("timeout", request=request, response=response)
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.cv_waiting_document)
        message = FakeMessage(document=pdf_document())

        await handle_cv_document(message, state, FakeApiClient(error=error))

        assert await state.get_state() == ProfileSetupStates.cv_waiting_document.state
        assert "слишком много времени" in message.answers[-1][0]
        await storage.close()

    asyncio.run(scenario())


def test_typing_heartbeat_repeats_and_stops_after_success(monkeypatch) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingApi(FakeApiClient):
            async def create_profile_draft_from_cv(
                self, user_id: int, *, filename: str, content_type: str, content: bytes
            ) -> dict[str, object]:
                started.set()
                await release.wait()
                return draft()

        monkeypatch.setattr(cv_profile_module, "CV_TYPING_INTERVAL_SECONDS", 0.01)
        storage, state = make_state()
        message = FakeMessage(document=pdf_document())
        task = asyncio.create_task(handle_cv_document(message, state, BlockingApi()))
        await started.wait()
        await asyncio.sleep(0.03)
        assert len(message.bot.actions) >= 2

        release.set()
        await task
        actions_after_success = len(message.bot.actions)
        await asyncio.sleep(0.03)
        assert len(message.bot.actions) == actions_after_success
        await storage.close()

    asyncio.run(scenario())


def test_typing_heartbeat_stops_after_api_error_and_telegram_failure(monkeypatch) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        class FailingTypingBot(FakeBot):
            async def send_chat_action(self, *, chat_id: int, action: object) -> None:
                raise RuntimeError("Telegram typing unavailable")

        class BlockingErrorApi(FakeApiClient):
            async def create_profile_draft_from_cv(
                self, user_id: int, *, filename: str, content_type: str, content: bytes
            ) -> dict[str, object]:
                started.set()
                await release.wait()
                request = httpx.Request("POST", "http://api/users/7/profile/draft-from-cv")
                response = httpx.Response(504, json={"detail": "ai_timeout"}, request=request)
                raise httpx.HTTPStatusError("timeout", request=request, response=response)

        monkeypatch.setattr(cv_profile_module, "CV_TYPING_INTERVAL_SECONDS", 0.01)
        storage, state = make_state()
        message = FakeMessage(document=pdf_document(), bot=FailingTypingBot())
        task = asyncio.create_task(handle_cv_document(message, state, BlockingErrorApi()))
        await started.wait()
        await asyncio.sleep(0.03)

        release.set()
        await task
        assert await state.get_state() == ProfileSetupStates.cv_waiting_document.state
        assert "слишком много времени" in message.answers[-1][0]
        await storage.close()

    asyncio.run(scenario())


def test_main_menu_during_processing_discards_late_api_result(monkeypatch) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingApi(FakeApiClient):
            async def create_profile_draft_from_cv(
                self, user_id: int, *, filename: str, content_type: str, content: bytes
            ) -> dict[str, object]:
                started.set()
                await release.wait()
                return draft()

        monkeypatch.setattr(cv_profile_module, "CV_TYPING_INTERVAL_SECONDS", 0.01)
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.cv_waiting_document)
        upload = FakeMessage(document=pdf_document(), message_id=40)
        task = asyncio.create_task(handle_cv_document(upload, state, BlockingApi()))
        await started.wait()

        menu_message = FakeMessage(text=ADD_JOB_BUTTON, message_id=41)
        await main_menu_action(menu_message, state)
        actions_after_navigation = len(upload.bot.actions)
        await asyncio.sleep(0.03)
        assert len(upload.bot.actions) == actions_after_navigation
        release.set()
        await task

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert menu_message.answers[-1][0] == REQUEST_URL_MESSAGE
        assert all("🎯 Роли" not in answer[0] for answer in upload.answers)
        await storage.close()

    asyncio.run(scenario())


def test_stale_summary_callback_does_nothing() -> None:
    async def scenario() -> None:
        storage, state = make_state()
        await state.set_state(ProfileSetupStates.summary)
        await state.set_data(
            {**draft(), PROFILE_DRAFT_SOURCE: "cv", ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 99}
        )
        message = FakeMessage(message_id=98)
        api = FakeApiClient()

        await handle_profile_callback(FakeCallback("profile:save", message), state, api)

        assert api.put_calls == []
        assert await state.get_state() == ProfileSetupStates.summary.state
        assert message.answers == []
        await storage.close()

    asyncio.run(scenario())


def test_cancel_callback_without_active_message_id_is_stale_in_cv_states() -> None:
    async def scenario() -> None:
        for profile_state in (
            ProfileSetupStates.cv_waiting_document,
            ProfileSetupStates.cv_processing,
        ):
            storage, state = make_state()
            await state.set_state(profile_state)
            await state.update_data(token="unchanged")
            message = FakeMessage(message_id=91)
            api = FakeApiClient()

            await handle_profile_callback(FakeCallback("profile:cancel", message), state, api)

            assert await state.get_state() == profile_state.state
            assert await state.get_data() == {"token": "unchanged"}
            assert message.answers == []
            assert api.put_calls == []
            await storage.close()

    asyncio.run(scenario())


def make_document_update(
    *, update_id: int, text: str | None = None, command: bool = False
) -> Update:
    document = None
    if text is None:
        document = Document(
            file_id="file-id",
            file_unique_id="unique-id",
            file_name="resume.pdf",
            mime_type="application/pdf",
            file_size=100,
        )
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(),
            chat=Chat(id=456, type="private"),
            from_user=User(id=123, is_bot=False, first_name="Anna"),
            text=text,
            entities=(
                [MessageEntity(type="bot_command", offset=0, length=len(text))]
                if command and text is not None
                else []
            ),
            document=document,
        ),
    )


async def dispatcher_state(bot: Bot, state_name: str) -> FSMContext:
    context = FSMContext(
        storage=dp.storage,
        key=StorageKey(bot_id=bot.id, chat_id=456, user_id=123),
    )
    await context.clear()
    await context.set_state(state_name)
    return context


def test_dispatcher_routes_document_and_prioritizes_main_menu(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:cv-test-token")
        answers: list[str] = []
        api = FakeApiClient()
        removed_keyboards: list[tuple[int, int]] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            answers.append(text)
            return SimpleNamespace(message_id=len(answers))

        async def send_chat_action(bot: Bot, **kwargs: object) -> None:
            return None

        async def download(bot: Bot, file: object, destination: BytesIO, **kwargs: object) -> BytesIO:
            destination.write(b"%PDF-resume")
            return destination

        async def edit_message_reply_markup(
            bot: Bot, *, chat_id: int, message_id: int, reply_markup: object | None = None
        ) -> None:
            assert reply_markup is None
            removed_keyboards.append((chat_id, message_id))

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "send_chat_action", send_chat_action)
        monkeypatch.setattr(Bot, "download", download)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_message_reply_markup)
        monkeypatch.setattr(main_module, "api_client", api)
        state = await dispatcher_state(bot, ProfileSetupStates.cv_waiting_document.state)

        await dp.feed_update(bot, make_document_update(update_id=501))

        assert await state.get_state() == ProfileSetupStates.summary.state
        assert len(api.draft_calls) == 1
        assert answers[0] == CV_PROCESSING_MESSAGE

        await state.set_state(ProfileSetupStates.cv_waiting_document)
        await dp.feed_update(bot, make_document_update(update_id=502, text=ADD_JOB_BUTTON))

        assert await state.get_state() == AddJobStates.waiting_for_url.state
        assert answers[-1] == REQUEST_URL_MESSAGE
        assert len(api.draft_calls) == 1
        assert removed_keyboards == [(456, 2)]
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_routes_cv_field_edit_input_back_to_summary(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:cv-test-token")
        answers: list[str] = []
        removed_keyboards: list[tuple[int, int]] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            answers.append(text)
            return SimpleNamespace(message_id=len(answers))

        async def edit_message_reply_markup(
            bot: Bot, *, chat_id: int, message_id: int, reply_markup: object | None = None
        ) -> None:
            assert reply_markup is None
            removed_keyboards.append((chat_id, message_id))

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", edit_message_reply_markup)
        state = await dispatcher_state(bot, ProfileSetupStates.cv_edit_field.state)
        await state.set_data(
            {
                **draft(),
                PROFILE_DRAFT_SOURCE: "cv",
                CV_EDITING_FIELD: "skills",
                ACTIVE_PROFILE_PROMPT_MESSAGE_ID: 700,
            }
        )

        await dp.feed_update(bot, make_document_update(update_id=701, text="Python, Django"))

        assert await state.get_state() == ProfileSetupStates.summary.state
        assert (await state.get_data())["skills"] == ["Python", "Django"]
        assert removed_keyboards == [(456, 700)]
        assert "🧩 Навыки: Python, Django" in answers[-1]
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_cancel_in_cv_waiting_document(monkeypatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:cv-test-token")
        answers: list[str] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            answers.append(text)
            return SimpleNamespace(message_id=len(answers))

        monkeypatch.setattr(Message, "answer", answer)
        state = await dispatcher_state(bot, ProfileSetupStates.cv_waiting_document.state)

        await dp.feed_update(
            bot,
            make_document_update(update_id=601, text="/cancel", command=True),
        )

        assert await state.get_state() is None
        assert await state.get_data() == {}
        assert answers == [PROFILE_CANCELLED_MESSAGE]
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_cancel_during_processing_discards_late_result(monkeypatch) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        actions: list[object] = []

        class BlockingApi(FakeApiClient):
            async def create_profile_draft_from_cv(
                self, user_id: int, *, filename: str, content_type: str, content: bytes
            ) -> dict[str, object]:
                started.set()
                await release.wait()
                return draft()

        bot = Bot("123456:cv-test-token")
        answers: list[str] = []

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            answers.append(text)
            return SimpleNamespace(message_id=len(answers))

        async def send_chat_action(bot: Bot, **kwargs: object) -> None:
            actions.append(kwargs["action"])

        async def download(bot: Bot, file: object, destination: BytesIO, **kwargs: object) -> BytesIO:
            destination.write(b"%PDF-resume")
            return destination

        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "send_chat_action", send_chat_action)
        monkeypatch.setattr(Bot, "download", download)
        monkeypatch.setattr(main_module, "api_client", BlockingApi())
        monkeypatch.setattr(cv_profile_module, "CV_TYPING_INTERVAL_SECONDS", 0.01)
        state = await dispatcher_state(bot, ProfileSetupStates.cv_waiting_document.state)

        upload_task = asyncio.create_task(dp.feed_update(bot, make_document_update(update_id=602)))
        await started.wait()
        assert await state.get_state() == ProfileSetupStates.cv_processing.state

        await dp.feed_update(
            bot,
            make_document_update(update_id=603, text="/cancel", command=True),
        )
        actions_after_cancel = len(actions)
        await asyncio.sleep(0.03)
        assert len(actions) == actions_after_cancel
        release.set()
        await upload_task

        assert await state.get_state() is None
        assert await state.get_data() == {}
        assert PROFILE_CANCELLED_MESSAGE in answers
        assert all("🎯 Роли" not in answer for answer in answers)
        await bot.session.close()

    asyncio.run(scenario())
