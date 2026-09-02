"""Read-only saved applications section with one active inline message."""

import logging
from typing import cast

import httpx
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.api_client import BotApiClient
from app.jobs import (
    _show_match_summary,
    format_job_card,
    handle_add_job,
    remove_active_match_inline_keyboard,
)
from app.telegram_cleanup import is_message_not_modified

logger = logging.getLogger(__name__)

APPLICATIONS_BUTTON = "📋 Мои вакансии"
APPLICATIONS_MESSAGE_ID = "applications_message_id"
APPLICATIONS_OFFSET = "applications_offset"
PAGE_SIZE = 5
APPLICATIONS_EMPTY_MESSAGE = "Сохранённых вакансий пока нет."
APPLICATIONS_LOAD_ERROR_MESSAGE = "Не удалось загрузить вакансии. Попробуй ещё раз."
APPLICATION_NOT_FOUND_MESSAGE = "Вакансия больше недоступна."


def _workplace_label(value: object) -> str:
    return {"any": "Любой", "remote": "Удалённо", "hybrid": "Гибрид", "onsite": "На месте работодателя"}.get(value, "Не указан")


def _list_item_text(item: dict[str, object], index: int) -> str:
    title = item.get("title") if isinstance(item.get("title"), str) else "Без названия"
    company = item.get("company") if isinstance(item.get("company"), str) else "Компания не указана"
    location = item.get("location") if isinstance(item.get("location"), str) else "Локация не указана"
    return f"{index}. {title} — {company}\n📍 {location} · {_workplace_label(item.get('workplace_type'))}"


def applications_list_keyboard(items: list[dict[str, object]], *, offset: int, has_next: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        app_id = item.get("app_id")
        if isinstance(app_id, int) and not isinstance(app_id, bool):
            title = item.get("title") if isinstance(item.get("title"), str) else "Без названия"
            rows.append([InlineKeyboardButton(text=title[:55], callback_data=f"applications:open:{app_id}:{offset}")])
    navigation: list[InlineKeyboardButton] = []
    if offset > 0:
        navigation.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"applications:page:{max(0, offset - PAGE_SIZE)}"))
    if has_next:
        navigation.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"applications:page:{offset + PAGE_SIZE}"))
    if navigation:
        rows.append(navigation)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def application_detail_keyboard(application_id: int, offset: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Почему подходит?", callback_data=f"applications:match:{application_id}")],
        [InlineKeyboardButton(text="⬅️ К списку", callback_data=f"applications:page:{offset}")],
    ])


def empty_applications_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💼 Добавить вакансию", callback_data="applications:add")
    ]])


async def show_applications_list(
    message: Message,
    state: FSMContext,
    api_client: BotApiClient,
    *,
    offset: int = 0,
    user_id: int | None = None,
) -> None:
    try:
        if user_id is None:
            if message.from_user is None:
                return
            user_id = await api_client.create_or_get_user(message.from_user)
        page = await api_client.list_applications(user_id, limit=PAGE_SIZE, offset=offset)
    except httpx.HTTPError:
        logger.warning("Could not load applications through API", exc_info=True)
        await _replace_or_send(message, state, APPLICATIONS_LOAD_ERROR_MESSAGE, None)
        return
    items = cast(list[dict[str, object]], page["items"])
    if not items and offset > 0:
        await show_applications_list(
            message, state, api_client, offset=max(0, offset - PAGE_SIZE), user_id=user_id
        )
        return
    if not items:
        await _replace_or_send(message, state, APPLICATIONS_EMPTY_MESSAGE, empty_applications_keyboard())
        await state.update_data(**{APPLICATIONS_OFFSET: 0})
        return
    text = "📋 Мои вакансии\n\n" + "\n\n".join(
        _list_item_text(item, offset + index + 1) for index, item in enumerate(items)
    )
    await _replace_or_send(
        message, state, text, applications_list_keyboard(items, offset=offset, has_next=bool(page["has_next"]))
    )
    await state.update_data(**{APPLICATIONS_OFFSET: offset})


async def _replace_or_send(message: Message, state: FSMContext, text: str, markup: InlineKeyboardMarkup | None) -> None:
    active_id = (await state.get_data()).get(APPLICATIONS_MESSAGE_ID)
    if active_id == message.message_id:
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except TelegramAPIError:
            logger.warning("Could not edit applications message", exc_info=True)
            await _remove_applications_inline_keyboard(message, active_id)
    sent = await message.answer(text, reply_markup=markup)
    await state.update_data(**{APPLICATIONS_MESSAGE_ID: sent.message_id})


async def handle_applications_menu(message: Message, state: FSMContext, api_client: BotApiClient) -> None:
    await show_applications_list(message, state, api_client)


async def handle_applications_callback(callback: CallbackQuery, state: FSMContext, api_client: BotApiClient) -> None:
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    message = cast(Message, callback.message)
    if (await state.get_data()).get(APPLICATIONS_MESSAGE_ID) != message.message_id:
        return
    data = callback.data or ""
    if data == "applications:add":
        await remove_active_applications_inline_keyboard(message, state)
        await handle_add_job(message, state)
        return
    try:
        user_id = await api_client.create_or_get_user(callback.from_user)
    except httpx.HTTPError:
        logger.warning("Could not resolve user for applications callback", exc_info=True)
        return
    if data.startswith("applications:page:"):
        offset_text = data.removeprefix("applications:page:")
        if offset_text.isdecimal():
            await remove_active_match_inline_keyboard(message, state)
            await show_applications_list(message, state, api_client, offset=int(offset_text), user_id=user_id)
        return
    if data.startswith("applications:open:"):
        parts = data.split(":")
        if len(parts) != 4 or not parts[2].isdecimal() or not parts[3].isdecimal():
            return
        await _show_application_detail(
            message, state, api_client, user_id, int(parts[2]), int(parts[3])
        )
        return
    if data.startswith("applications:match:"):
        app_id = data.removeprefix("applications:match:")
        if not app_id.isdecimal():
            return
        await remove_active_match_inline_keyboard(message, state)
        await _show_match_summary(message, state, api_client, user_id, int(app_id))


async def _show_application_detail(
    message: Message,
    state: FSMContext,
    api_client: BotApiClient,
    user_id: int,
    application_id: int,
    offset: int,
) -> None:
    try:
        detail = await api_client.get_application(user_id, application_id)
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            await _replace_or_send(message, state, APPLICATION_NOT_FOUND_MESSAGE, None)
        else:
            logger.warning("Could not load application detail", exc_info=True)
        return
    except httpx.HTTPError:
        logger.warning("Could not load application detail", exc_info=True)
        return
    job = detail["job"]
    assert isinstance(job, dict)
    await _replace_or_send(message, state, format_job_card(job) or "Вакансия без данных.", application_detail_keyboard(application_id, offset))


async def remove_active_applications_inline_keyboard(message: Message, state: FSMContext) -> None:
    message_id = (await state.get_data()).get(APPLICATIONS_MESSAGE_ID)
    await _remove_applications_inline_keyboard(message, message_id)


async def _remove_applications_inline_keyboard(message: Message, message_id: object) -> None:
    if not isinstance(message_id, int) or message.bot is None:
        return
    try:
        await message.bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=message_id, reply_markup=None)
    except TelegramAPIError as error:
        if is_message_not_modified(error):
            return
        logger.warning("Could not remove applications keyboard", exc_info=True)
