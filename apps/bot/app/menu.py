import logging
from collections.abc import Callable
from typing import cast

import httpx
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from app.api_client import BotApiClient
from app.cv_profile import handle_cv_profile_setup
from app.jobs import handle_add_job, remove_active_match_inline_keyboard
from app.profile import (
    PERSISTED_PROFILE_SNAPSHOT,
    PROFILE_SECTION_EDIT_CALLBACK,
    PROFILE_SECTION_MESSAGE_ID,
    handle_profile_setup,
    remove_active_profile_inline_keyboard,
    show_persisted_profile_editor,
    show_saved_profile_card,
)
from app.telegram_cleanup import is_message_not_modified

logger = logging.getLogger(__name__)

ADD_JOB_BUTTON = "💼 Добавить вакансию"
PROFILE_BUTTON = "👤 Мой профиль"
MENU_ACTIONS = {ADD_JOB_BUTTON, PROFILE_BUTTON}
PROFILE_SETUP_CALLBACK = "profile_section:setup"
PROFILE_CV_CALLBACK = "profile_section:cv"
PROFILE_MISSING_MESSAGE = "Профиль пока не заполнен"
PROFILE_LOAD_ERROR_MESSAGE = "Не удалось загрузить профиль. Попробуй ещё раз."


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ADD_JOB_BUTTON)],
            [KeyboardButton(text=PROFILE_BUTTON)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def missing_profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Заполнить вручную",
                    callback_data=PROFILE_SETUP_CALLBACK,
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Заполнить из CV",
                    callback_data=PROFILE_CV_CALLBACK,
                )
            ],
        ]
    )


async def main_menu_action(
    message: Message, state: FSMContext, api_client: BotApiClient | None = None
) -> None:
    await remove_active_profile_section_keyboard(message, state)
    await remove_active_profile_inline_keyboard(message, state)
    await remove_active_match_inline_keyboard(message, state)
    await state.clear()
    if message.text == ADD_JOB_BUTTON:
        await handle_add_job(message, state)
    elif message.text == PROFILE_BUTTON:
        if api_client is None or message.from_user is None:
            await message.answer(PROFILE_LOAD_ERROR_MESSAGE)
            return
        try:
            user_id = await api_client.create_or_get_user(message.from_user)
            profile = await api_client.get_user_profile(user_id)
        except httpx.HTTPError:
            logger.exception("Could not load user profile through API")
            await message.answer(PROFILE_LOAD_ERROR_MESSAGE)
            return
        if profile is not None:
            await show_saved_profile_card(message, state, profile)
            return
        section_message = await message.answer(
            PROFILE_MISSING_MESSAGE, reply_markup=missing_profile_keyboard()
        )
        await state.update_data(**{PROFILE_SECTION_MESSAGE_ID: section_message.message_id})


async def profile_section_setup(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    message = cast(Message, callback.message)
    if not await is_active_profile_section_message(message, state):
        return
    await remove_active_profile_section_keyboard(message, state)
    await handle_profile_setup(message, state)


async def profile_section_cv(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    message = cast(Message, callback.message)
    if not await is_active_profile_section_message(message, state):
        return
    await remove_active_profile_section_keyboard(message, state)
    await handle_cv_profile_setup(message, state)


async def profile_section_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    message = cast(Message, callback.message)
    if not await is_active_profile_section_message(message, state):
        return
    if not isinstance((await state.get_data()).get(PERSISTED_PROFILE_SNAPSHOT), dict):
        return
    await remove_active_profile_section_keyboard(message, state)
    await show_persisted_profile_editor(message, state)


async def is_active_profile_section_message(message: Message, state: FSMContext) -> bool:
    return (await state.get_data()).get(PROFILE_SECTION_MESSAGE_ID) == message.message_id


async def remove_active_profile_section_keyboard(message: Message, state: FSMContext) -> None:
    section_message_id = (await state.get_data()).get(PROFILE_SECTION_MESSAGE_ID)
    if not isinstance(section_message_id, int) or message.bot is None:
        return
    try:
        await message.bot.edit_message_reply_markup(
            chat_id=message.chat.id, message_id=section_message_id, reply_markup=None
        )
    except TelegramBadRequest as error:
        if is_message_not_modified(error):
            return
        logger.warning("Could not remove active profile section keyboard", exc_info=True)
    except TelegramAPIError:
        logger.warning("Could not remove active profile section keyboard", exc_info=True)


def register_main_menu_handlers(
    router: Router, api_client_provider: Callable[[], BotApiClient]
) -> None:
    async def registered_main_menu_action(message: Message, state: FSMContext) -> None:
        await main_menu_action(message, state, api_client_provider())

    router.message.register(registered_main_menu_action, F.text.in_(MENU_ACTIONS), StateFilter("*"))
    router.callback_query.register(profile_section_setup, F.data == PROFILE_SETUP_CALLBACK)
    router.callback_query.register(profile_section_cv, F.data == PROFILE_CV_CALLBACK)
    router.callback_query.register(profile_section_edit, F.data == PROFILE_SECTION_EDIT_CALLBACK)
