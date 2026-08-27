import asyncio
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

from app.api_client import JobHunterApiClient
from app.jobs import AddJobStates, handle_add_job, handle_cancel, handle_job_url
from app.start import handle_start

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

dp = Dispatcher(storage=MemoryStorage())
api_client = JobHunterApiClient(API_BASE_URL)


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await handle_start(message, api_client)


@dp.message(Command("add_job"))
async def add_job(message: Message, state: FSMContext) -> None:
    await handle_add_job(message, state)


@dp.message(Command("cancel"), StateFilter("*"))
async def cancel(message: Message, state: FSMContext) -> None:
    await handle_cancel(message, state)


@dp.message(AddJobStates.waiting_for_url, F.text)
async def receive_job_url(message: Message, state: FSMContext) -> None:
    await handle_job_url(message, state, api_client)


async def main() -> None:
    bot = Bot(TOKEN)
    try:
        await dp.start_polling(bot)
    finally:
        await api_client.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
