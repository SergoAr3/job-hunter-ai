import asyncio
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import CallbackQuery, Message

from app.api_client import JobHunterApiClient
from app.cv_profile import handle_cv_document, handle_unsupported_cv_message
from app.jobs import AddJobStates, handle_add_job, handle_cancel, handle_job_url, handle_match_callback
from app.applications import ApplicationsStates, handle_applications_callback, handle_note_cancel, handle_note_text
from app.applications import handle_next_action_cancel, handle_next_action_non_text, handle_next_action_text
from app.applications import APPLICATIONS_SORT_VIEW, APPLICATIONS_VIEW, handle_sort_cancel
from app.applications import handle_search_cancel, handle_search_non_text, handle_search_text
from app.applications import handle_letter_cancel, handle_letter_language_text
from app.menu import register_main_menu_handlers
from app.profile import (
    ProfileSetupStates,
    handle_languages,
    handle_profile_draft_field_input,
    handle_location,
    handle_profile_callback,
    handle_profile_cancel,
    handle_profile_setup,
    handle_salary,
    handle_skills,
    handle_target_roles,
    is_profile_state,
    remove_active_profile_inline_keyboard,
)
from app.start import handle_start

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
api_client = JobHunterApiClient(API_BASE_URL)


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await handle_start(message, api_client)


@dp.message(Command("add_job"))
async def add_job(message: Message, state: FSMContext) -> None:
    await handle_add_job(message, state)


@dp.message(Command("profile_setup"))
async def profile_setup(message: Message, state: FSMContext) -> None:
    await handle_profile_setup(message, state)


@dp.message(Command("cancel"), StateFilter("*"))
async def cancel(message: Message, state: FSMContext) -> None:
    if str((await state.get_data()).get(APPLICATIONS_VIEW)).startswith("letter"):
        await handle_letter_cancel(message, state, api_client)
    elif (await state.get_data()).get(APPLICATIONS_VIEW) == APPLICATIONS_SORT_VIEW:
        await handle_sort_cancel(message, state, api_client)
    elif await state.get_state() == ApplicationsStates.waiting_for_search.state:
        await handle_search_cancel(message, state, api_client)
    elif await state.get_state() == ApplicationsStates.waiting_for_note.state:
        await handle_note_cancel(message, state, api_client)
    elif await state.get_state() in (ApplicationsStates.waiting_for_next_action.state, ApplicationsStates.waiting_for_next_action_due_on.state):
        await handle_next_action_cancel(message, state, api_client)
    elif is_profile_state(await state.get_state()):
        await remove_active_profile_inline_keyboard(message, state)
        await handle_profile_cancel(message, state)
    else:
        await handle_cancel(message, state)


register_main_menu_handlers(dp, lambda: api_client)


@dp.message(ApplicationsStates.waiting_for_letter_language)
async def receive_letter_language(message: Message, state: FSMContext) -> None:
    await handle_letter_language_text(message, state, api_client)


@dp.message(ApplicationsStates.waiting_for_search, F.text)
async def receive_search_text(message: Message, state: FSMContext) -> None:
    await handle_search_text(message, state, api_client)


@dp.message(ApplicationsStates.waiting_for_search)
async def receive_search_non_text(message: Message, state: FSMContext) -> None:
    await handle_search_non_text(message, state)


@dp.message(StateFilter(ApplicationsStates.waiting_for_next_action, ApplicationsStates.waiting_for_next_action_due_on), F.text)
async def receive_next_action_text(message: Message, state: FSMContext) -> None:
    await handle_next_action_text(message, state, api_client)


@dp.message(StateFilter(ApplicationsStates.waiting_for_next_action, ApplicationsStates.waiting_for_next_action_due_on))
async def receive_next_action_non_text(message: Message, state: FSMContext) -> None:
    await handle_next_action_non_text(message, state)


@dp.message(ApplicationsStates.waiting_for_note, F.text)
async def receive_note_text(message: Message, state: FSMContext) -> None:
    await handle_note_text(message, state, api_client)


@dp.message(ApplicationsStates.waiting_for_note)
async def receive_note_non_text(message: Message) -> None:
    await message.answer("Отправь текст заметки или нажми Отмена.")


@dp.message(AddJobStates.waiting_for_url, F.text)
async def receive_job_url(message: Message, state: FSMContext) -> None:
    await handle_job_url(message, state, api_client)


@dp.message(ProfileSetupStates.target_roles, F.text)
async def receive_target_roles(message: Message, state: FSMContext) -> None:
    await handle_target_roles(message, state)


@dp.message(ProfileSetupStates.skills, F.text)
async def receive_skills(message: Message, state: FSMContext) -> None:
    await handle_skills(message, state, api_client)


@dp.message(ProfileSetupStates.location, F.text)
async def receive_location(message: Message, state: FSMContext) -> None:
    await handle_location(message, state)


@dp.message(ProfileSetupStates.salary, F.text)
async def receive_salary(message: Message, state: FSMContext) -> None:
    await handle_salary(message, state)


@dp.message(ProfileSetupStates.languages, F.text)
async def receive_languages(message: Message, state: FSMContext) -> None:
    await handle_languages(message, state, api_client)


@dp.message(ProfileSetupStates.edit_field, F.text)
async def receive_profile_draft_field_input(message: Message, state: FSMContext) -> None:
    await handle_profile_draft_field_input(message, state, api_client)


@dp.message(ProfileSetupStates.cv_waiting_document, F.document)
async def receive_cv_document(message: Message, state: FSMContext) -> None:
    await handle_cv_document(message, state, api_client)


@dp.message(ProfileSetupStates.cv_waiting_document)
async def receive_unsupported_cv_message(message: Message, state: FSMContext) -> None:
    await handle_unsupported_cv_message(message, state)


@dp.callback_query(F.data.startswith("profile:"))
async def profile_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await handle_profile_callback(callback, state, api_client)


@dp.callback_query(F.data.startswith("match:"))
async def match_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await handle_match_callback(callback, state, api_client)


@dp.callback_query(F.data.startswith("applications:"))
async def applications_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await handle_applications_callback(callback, state, api_client)


async def main() -> None:
    bot = Bot(TOKEN)
    try:
        await dp.start_polling(bot)
    finally:
        await api_client.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
