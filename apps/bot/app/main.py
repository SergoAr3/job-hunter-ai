import asyncio
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.api_client import JobHunterApiClient
from app.start import handle_start

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

dp = Dispatcher()
api_client = JobHunterApiClient(API_BASE_URL)


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await handle_start(message, api_client)


async def main() -> None:
    bot = Bot(TOKEN)
    try:
        await dp.start_polling(bot)
    finally:
        await api_client.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
