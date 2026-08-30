import logging

import httpx
from aiogram.types import Message

from app.api_client import BotApiClient
from app.menu import main_menu_keyboard

logger = logging.getLogger(__name__)

API_UNAVAILABLE_MESSAGE = "Сервис временно недоступен. Попробуйте ещё раз позже."


async def handle_start(message: Message, api_client: BotApiClient) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return

    try:
        await api_client.create_or_get_user(telegram_user)
    except httpx.HTTPError:
        logger.exception("Could not create or get Telegram user through API")
        await message.answer(API_UNAVAILABLE_MESSAGE)
        return

    await message.answer(
        f"Привет, {telegram_user.first_name}! Я помогу с поиском работы.",
        reply_markup=main_menu_keyboard(),
    )
