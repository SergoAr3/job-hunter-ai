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
    if is_profile_state(await state.get_state()):
        await remove_active_profile_inline_keyboard(message, state)
        await handle_profile_cancel(message, state)
    else:
        await handle_cancel(message, state)


register_main_menu_handlers(dp, lambda: api_client)


@dp.message(AddJobStates.waiting_for_url, F.text)
async def receive_job_url(message: Message, state: FSMContext) -> None:
    await handle_job_url(message, state, api_client)


@dp.message(ProfileSetupStates.target_roles, F.text)
async def receive_target_roles(message: Message, state: FSMContext) -> None:
    await handle_target_roles(message, state)


@dp.message(ProfileSetupStates.skills, F.text)
async def receive_skills(message: Message, state: FSMContext) -> None:
    await handle_skills(message, state)


@dp.message(ProfileSetupStates.location, F.text)
async def receive_location(message: Message, state: FSMContext) -> None:
    await handle_location(message, state)


@dp.message(ProfileSetupStates.salary, F.text)
async def receive_salary(message: Message, state: FSMContext) -> None:
    await handle_salary(message, state)


@dp.message(ProfileSetupStates.languages, F.text)
async def receive_languages(message: Message, state: FSMContext) -> None:
    await handle_languages(message, state)


@dp.message(ProfileSetupStates.edit_field, F.text)
async def receive_profile_draft_field_input(message: Message, state: FSMContext) -> None:
    await handle_profile_draft_field_input(message, state)


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


async def main() -> None:
    bot = Bot(TOKEN)
    try:
        await dp.start_polling(bot)
    finally:
        await api_client.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
