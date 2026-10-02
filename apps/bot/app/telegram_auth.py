"""Thin Telegram confirmation UI; ownership and identity decisions belong to API."""
import logging
import re
import httpx
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)
KEY = "telegram_auth_context"
INVALID = "Запрос недействителен или уже завершён. Начните заново на сайте."
UNAVAILABLE = "Сервис временно недоступен. Попробуйте ещё раз позже."


async def remove_auth_keyboard(message, state):
    context = (await state.get_data()).get(KEY)
    if isinstance(context, dict):
        try:
            await message.bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=context["message_id"], reply_markup=None)
        except TelegramAPIError:
            logger.warning("Could not remove Telegram auth keyboard")
        data = await state.get_data()
        data.pop(KEY, None)
        await state.set_data(data)


async def handle_auth_start(message, state, api_client, payload):
    await remove_auth_keyboard(message, state)
    # Clean existing interaction keyboards before leaving the previous flow.
    from app.menu import remove_active_profile_section_keyboard
    from app.profile import remove_active_profile_inline_keyboard
    from app.discover import remove_active_discover_inline_keyboard
    from app.applications import remove_active_applications_inline_keyboard
    from app.jobs import remove_active_match_inline_keyboard
    for cleanup in (remove_active_profile_section_keyboard, remove_active_profile_inline_keyboard, remove_active_discover_inline_keyboard, remove_active_applications_inline_keyboard, remove_active_match_inline_keyboard):
        await cleanup(message, state)
    await state.clear()
    if not message.from_user or message.chat.type != "private" or message.chat.id != message.from_user.id or not re.fullmatch(r"auth_[A-Za-z0-9_-]{43}", payload):
        await message.answer(INVALID)
        return
    token = payload[5:]
    try:
        info = await api_client.inspect_telegram_challenge(token)
    except httpx.HTTPStatusError as error:
        await message.answer(INVALID if error.response.status_code < 500 else UNAVAILABLE)
        return
    except httpx.HTTPError:
        await message.answer(UNAVAILABLE)
        return
    if info["status"] != "pending":
        await message.answer(INVALID)
        return
    prompt_text = "Подтвердить вход в Job Hunter AI?" if info["purpose"] == "login" else "Подтвердить подключение Telegram к аккаунту на сайте?"
    code = info["code"]
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Подтвердить", callback_data=f"tga:a:{code}"),
        InlineKeyboardButton(text="Отмена", callback_data=f"tga:c:{code}"),
    ]])
    prompt = await message.answer(f"{prompt_text}\nКод сверки: {code}\nПодтверждайте только запрос, который вы начали сами, с тем же кодом в браузере. Никому не пересылайте ссылку.", reply_markup=markup)
    await state.update_data(**{KEY:{"token":token,"code":code,"message_id":prompt.message_id,"purpose":info["purpose"]}})


async def handle_auth_callback(callback, state, api_client):
    context = (await state.get_data()).get(KEY)
    match = re.fullmatch(r"tga:([ac]):([A-F0-9]{6})", callback.data or "")
    if (not isinstance(context, dict) or match is None or context.get("code") != match.group(2)
        or callback.message is None or callback.message.message_id != context.get("message_id")
        or callback.message.chat.type != "private" or callback.message.chat.id != callback.from_user.id):
        await callback.answer("Эта кнопка уже неактуальна.")
        return
    try:
        # from_user is the actual callback actor, never the Bot message sender.
        await api_client.decide_telegram_challenge(context["token"], callback.from_user, match.group(1) == "a")
    except httpx.HTTPStatusError as error:
        if error.response.status_code >= 500:
            await callback.answer(UNAVAILABLE)
            return
        await remove_auth_keyboard(callback.message, state)
        await callback.answer(INVALID)
        return
    except httpx.HTTPError:
        await callback.answer(UNAVAILABLE)
        return
    await remove_auth_keyboard(callback.message, state)
    await callback.answer("Подтверждено" if match.group(1) == "a" else "Отменено")
    if context.get("purpose") == "login":
        await callback.message.answer("Вход подтверждён. Вернитесь на сайт." if match.group(1) == "a" else "Вход отменён.")
    else:
        await callback.message.answer("Вернитесь в тот же браузер для завершения." if match.group(1) == "a" else "Запрос отменён.")
