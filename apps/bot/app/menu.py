import logging
from typing import cast

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
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

from app.jobs import handle_add_job
from app.profile import handle_profile_setup, remove_active_profile_inline_keyboard

logger = logging.getLogger(__name__)

ADD_JOB_BUTTON = "💼 Добавить вакансию"
PROFILE_BUTTON = "👤 Мой профиль"
MENU_ACTIONS = {ADD_JOB_BUTTON, PROFILE_BUTTON}
PROFILE_SETUP_CALLBACK = "profile_section:setup"
PROFILE_SECTION_MESSAGE_ID = "profile_section_message_id"
PROFILE_SECTION_MESSAGE = (
    "👤 Мой профиль\n\n"
    "Профиль помогает подбирать вакансии под твой опыт и предпочтения."
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ADD_JOB_BUTTON)],
            [KeyboardButton(text=PROFILE_BUTTON)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def profile_section_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Заполнить / изменить профиль",
                    callback_data=PROFILE_SETUP_CALLBACK,
                )
            ]
        ]
    )


async def main_menu_action(message: Message, state: FSMContext) -> None:
    await remove_active_profile_section_keyboard(message, state)
    await remove_active_profile_inline_keyboard(message, state)
    await state.clear()
    if message.text == ADD_JOB_BUTTON:
        await handle_add_job(message, state)
    elif message.text == PROFILE_BUTTON:
        section_message = await message.answer(
            PROFILE_SECTION_MESSAGE, reply_markup=profile_section_keyboard()
        )
        await state.update_data(profile_section_message_id=section_message.message_id)


async def profile_section_setup(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    message = cast(Message, callback.message)
    if not await is_active_profile_section_message(message, state):
        return
    await remove_active_profile_section_keyboard(message, state)
    await handle_profile_setup(message, state)


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
    except TelegramAPIError:
        logger.warning("Could not remove active profile section keyboard", exc_info=True)


def register_main_menu_handlers(router: Router) -> None:
    router.message.register(main_menu_action, F.text.in_(MENU_ACTIONS), StateFilter("*"))
    router.callback_query.register(profile_section_setup, F.data == PROFILE_SETUP_CALLBACK)
