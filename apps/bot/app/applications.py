"""Saved applications and status controls with one active inline message."""

import logging
import re
import secrets
from datetime import date, datetime, timezone
from typing import Any, cast

import httpx
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from app.api_client import BotApiClient
from app.jobs import (
    format_job_card,
    format_match_message,
    handle_add_job,
    remove_active_match_inline_keyboard,
)
from app.telegram_cleanup import is_message_not_modified

logger = logging.getLogger(__name__)

APPLICATIONS_BUTTON = "📋 Мои вакансии"
APPLICATIONS_MESSAGE_ID = "applications_message_id"
APPLICATIONS_OFFSET = "applications_offset"
APPLICATIONS_VIEW = "applications_view"
APPLICATIONS_APPLICATION_ID = "applications_application_id"
APPLICATIONS_LIST_VIEW = "list"
APPLICATIONS_DETAIL_VIEW = "detail"
APPLICATIONS_MATCH_VIEW = "match"
APPLICATIONS_HISTORY_VIEW = "history"
APPLICATIONS_STATUS_VIEW = "status_picker"
APPLICATIONS_STATUS_TOKEN = "applications_status_token"
APPLICATIONS_FILTER_STATUS = "applications_filter_status"
APPLICATIONS_SEARCH_QUERY = "applications_search_query"
APPLICATIONS_SEARCH_TOKEN = "applications_search_token"
APPLICATIONS_LIST_TOKEN = "applications_list_token"
APPLICATIONS_FILTER_VIEW = "filter_picker"
APPLICATIONS_SORT = "applications_sort"
APPLICATIONS_SORT_VIEW = "sort_picker"
STATUS_LABELS = {
    "saved": "Сохранена", "applied": "Откликнулся", "interview": "Собеседование",
    "rejected": "Отказ", "offer": "Оффер",
}
SORT_LABELS = {
    "newest": "Сначала новые",
    "oldest": "Сначала старые",
    "next_action": "Ближайшее действие",
}
SORT_PICKER_LABELS = {
    "newest": "🆕 Сначала новые",
    "oldest": "🕰 Сначала старые",
    "next_action": "📅 Ближайшее действие",
}
SORT_CALLBACK_VALUES = {"n": "newest", "o": "oldest", "a": "next_action"}
PAGE_SIZE = 5
APPLICATIONS_EMPTY_MESSAGE = "Сохранённых вакансий пока нет."
APPLICATIONS_LOAD_ERROR_MESSAGE = "Не удалось загрузить вакансии. Попробуй ещё раз."
APPLICATION_NOT_FOUND_MESSAGE = "Вакансия больше недоступна."
APPLICATIONS_NOTE_TOKEN = "applications_note_token"
APPLICATIONS_NOTE_VIEW = "note_input"
APPLICATIONS_NEXT_ACTION_TOKEN = "applications_next_action_token"
APPLICATIONS_NEXT_ACTION_DRAFT = "applications_next_action_draft"


class ApplicationsStates(StatesGroup):
    waiting_for_search = State()
    waiting_for_note = State()
    waiting_for_next_action = State()
    waiting_for_next_action_due_on = State()


def _workplace_label(value: object) -> str:
    return {"any": "Любой", "remote": "Удалённо", "hybrid": "Гибрид", "onsite": "На месте работодателя"}.get(value, "Не указан")


def _list_item_text(item: dict[str, object], index: int) -> str:
    title = item.get("title") if isinstance(item.get("title"), str) else "Без названия"
    company = item.get("company") if isinstance(item.get("company"), str) else "Компания не указана"
    location = item.get("location") if isinstance(item.get("location"), str) else "Локация не указана"
    return f"{index}. {title} — {company}\n📍 {location} · {_workplace_label(item.get('workplace_type'))}\nСтатус: {STATUS_LABELS.get(str(item.get('status')), 'Не указан')}"


def applications_list_keyboard(
    items: list[dict[str, object]], *, offset: int, has_next: bool, token: str, status: str | None,
    q: str | None, sort: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        app_id = item.get("app_id")
        if isinstance(app_id, int) and not isinstance(app_id, bool):
            title = item.get("title") if isinstance(item.get("title"), str) else "Без названия"
            rows.append([InlineKeyboardButton(text=title[:55], callback_data=f"applications:list:{token}:open:{app_id}")])
    navigation: list[InlineKeyboardButton] = []
    if offset > 0:
        navigation.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"applications:list:{token}:page:{max(0, offset - PAGE_SIZE)}"))
    if has_next:
        navigation.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"applications:list:{token}:page:{offset + PAGE_SIZE}"))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(
        text=f"Фильтр: {STATUS_LABELS[status] if status else 'Все'}",
        callback_data=f"applications:list:{token}:filter",
    )])
    rows.append([InlineKeyboardButton(
        text=f"Сортировка: {SORT_LABELS[sort]}",
        callback_data=f"applications:list:{token}:sort",
    )])
    rows.append([InlineKeyboardButton(
        text="🔎 Поиск", callback_data=f"applications:list:{token}:search",
    )])
    if q is not None:
        rows.append([InlineKeyboardButton(
            text="✖️ Сбросить поиск", callback_data=f"applications:list:{token}:reset_search",
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _search_prompt_text(error: str | None = None) -> str:
    text = "Отправь название вакансии или компании. Для отмены — /cancel."
    return f"{text}\n\n⚠️ {error}" if error else text


def _search_prompt_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отмена", callback_data=f"applications:search_cancel:{token}")
    ]])


def _committed_sort(context: dict[str, object]) -> str:
    value = context.get(APPLICATIONS_SORT)
    return value if isinstance(value, str) and value in SORT_LABELS else "newest"


def _sort_picker_content(
    token: str, current_sort: str, error: str | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    text = "Выбери сортировку вакансий:"
    if error:
        text += f"\n\n⚠️ {error}"
    reverse_values = {value: key for key, value in SORT_CALLBACK_VALUES.items()}
    rows = [[InlineKeyboardButton(
        text=("✓ " if value == current_sort else "") + label,
        callback_data=f"applications:list:{token}:sort_set:{reverse_values[value]}",
    )] for value, label in SORT_PICKER_LABELS.items()]
    rows.append([InlineKeyboardButton(
        text="Отмена", callback_data=f"applications:list:{token}:sort_cancel",
    )])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def application_detail_keyboard(application_id: int, offset: int, *, has_note: bool = False, has_next_action: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить статус", callback_data=f"applications:status:{application_id}:{offset}")],
        [InlineKeyboardButton(text="📅 Следующее действие", callback_data=f"applications:next_action:{application_id}:{offset}")],
        *([[InlineKeyboardButton(text="🗑 Удалить следующее действие", callback_data=f"applications:next_action_delete:{application_id}:{offset}")]] if has_next_action else []),
        [InlineKeyboardButton(text="📝 Заметка", callback_data=f"applications:note:{application_id}:{offset}")],
        *([[InlineKeyboardButton(text="🗑 Удалить заметку", callback_data=f"applications:note_delete:{application_id}:{offset}")]] if has_note else []),
        [InlineKeyboardButton(text="🕘 История статусов", callback_data=f"applications:history:{application_id}:{offset}")],
        [InlineKeyboardButton(text="🔎 Почему подходит?", callback_data=f"applications:match:{application_id}:{offset}")],
        [InlineKeyboardButton(text="⬅️ К списку", callback_data=f"applications:page:{offset}")],
    ])


def application_match_keyboard(application_id: int, offset: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ К вакансии", callback_data=f"applications:detail:{application_id}:{offset}")],
        [InlineKeyboardButton(text="📋 К списку", callback_data=f"applications:page:{offset}")],
    ])


def application_history_keyboard(application_id: int, offset: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="⬅️ К вакансии",
            callback_data=f"applications:detail:{application_id}:{offset}",
        )
    ]])


async def show_applications_list(
    message: Message,
    state: FSMContext,
    api_client: BotApiClient,
    *,
    offset: int = 0,
    user_id: int | None = None,
    q: str | None = None,
    commit_search_query: bool = False,
    search_failure_prompt: bool = False,
) -> bool:
    await state.update_data({APPLICATIONS_STATUS_TOKEN: None, APPLICATIONS_LIST_TOKEN: None})
    context = await state.get_data()
    status = cast(str | None, context.get(APPLICATIONS_FILTER_STATUS))
    search_query = q if commit_search_query else cast(str | None, context.get(APPLICATIONS_SEARCH_QUERY))
    sort = _committed_sort(context)
    try:
        if user_id is None:
            if message.from_user is None:
                return False
            user_id = await api_client.create_or_get_user(message.from_user)
        while True:
            page = await api_client.list_applications(
                user_id, limit=PAGE_SIZE, offset=offset, status=status, q=search_query, sort=sort
            )
            items = cast(list[dict[str, object]], page["items"])
            if items or offset == 0:
                break
            offset = max(0, offset - PAGE_SIZE)
    except httpx.HTTPError:
        logger.warning("Could not load applications through API", exc_info=True)
        if search_failure_prompt:
            token = context.get(APPLICATIONS_SEARCH_TOKEN)
            if isinstance(token, str):
                await _replace_or_send(
                    message, state, _search_prompt_text("Не удалось выполнить поиск. Попробуй ещё раз."),
                    _search_prompt_keyboard(token), canonical_target=True,
                )
                return False
        await _replace_or_send(message, state, APPLICATIONS_LOAD_ERROR_MESSAGE, None)
        return False
    token = secrets.token_hex(4)
    text, markup = _applications_list_content(
        items, offset=offset, has_next=bool(page["has_next"]), token=token,
        status=status, q=search_query, sort=sort,
    )
    await _replace_or_send(message, state, text, markup)
    await state.update_data(
        **{
            APPLICATIONS_OFFSET: offset,
            APPLICATIONS_VIEW: APPLICATIONS_LIST_VIEW,
            APPLICATIONS_APPLICATION_ID: None,
            APPLICATIONS_LIST_TOKEN: token,
            APPLICATIONS_SEARCH_QUERY: search_query,
            APPLICATIONS_SEARCH_TOKEN: None,
            APPLICATIONS_SORT: sort,
        }
    )
    await state.set_state(None)
    return True


def _applications_list_content(
    items: list[dict[str, object]], *, offset: int, has_next: bool, token: str,
    status: str | None, q: str | None, sort: str,
) -> tuple[str, InlineKeyboardMarkup]:
    markup = applications_list_keyboard(
        items, offset=offset, has_next=has_next, token=token, status=status, q=q, sort=sort,
    )
    if not items:
        if q is not None:
            text = (
                f"По запросу «{q}» среди вакансий со статусом «{STATUS_LABELS[status]}» ничего не найдено."
                if status else f"По запросу «{q}» вакансий не найдено."
            )
        else:
            text = f"Вакансий со статусом «{STATUS_LABELS[status]}» пока нет." if status else APPLICATIONS_EMPTY_MESSAGE
        if status is None and q is None:
            markup.inline_keyboard.insert(0, [InlineKeyboardButton(
                text="💼 Добавить вакансию", callback_data=f"applications:list:{token}:add",
            )])
    else:
        header = f"📋 Мои вакансии\nСтатус: {STATUS_LABELS[status] if status else 'Все'}"
        if q is not None:
            header += f"\nПоиск: {q}"
        text = header + "\n\n" + "\n\n".join(
            _list_item_text(item, offset + index + 1) for index, item in enumerate(items)
        )
    return text, markup


async def _replace_or_send(
    message: Message,
    state: FSMContext,
    text: str,
    markup: InlineKeyboardMarkup | None,
    *,
    parse_mode: ParseMode | None = None,
    canonical_target: bool = False,
) -> None:
    active_id = (await state.get_data()).get(APPLICATIONS_MESSAGE_ID)
    if canonical_target and isinstance(active_id, int) and active_id != message.message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id, message_id=active_id, text=text,
                reply_markup=markup, parse_mode=parse_mode,
            )
            return
        except TelegramAPIError as error:
            if is_message_not_modified(error):
                return
            logger.warning("Could not edit applications message", exc_info=True)
            await _remove_applications_inline_keyboard(message, active_id)
    if active_id == message.message_id:
        try:
            if parse_mode is None and not canonical_target:
                await message.edit_text(text, reply_markup=markup)
            else:
                await message.edit_text(text, reply_markup=markup, parse_mode=parse_mode)
            return
        except TelegramAPIError as error:
            if is_message_not_modified(error):
                return
            logger.warning("Could not edit applications message", exc_info=True)
            await _remove_applications_inline_keyboard(message, active_id)
    if parse_mode is None and not canonical_target:
        sent = await message.answer(text, reply_markup=markup)
    else:
        sent = await message.answer(text, reply_markup=markup, parse_mode=parse_mode)
    await state.update_data(**{APPLICATIONS_MESSAGE_ID: sent.message_id})


async def _render_sort_transition(
    message: Message,
    state: FSMContext,
    text: str,
    markup: InlineKeyboardMarkup,
    commit_data: dict[str, Any],
) -> bool:
    """Render first, then atomically switch the sort interaction context."""
    active_id = (await state.get_data()).get(APPLICATIONS_MESSAGE_ID)
    try:
        if isinstance(active_id, int) and active_id != message.message_id:
            if message.bot is None:
                return False
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=active_id,
                text=text,
                reply_markup=markup,
            )
        else:
            await message.edit_text(text, reply_markup=markup)
    except TelegramAPIError as error:
        if isinstance(error, TelegramBadRequest) and is_message_not_modified(error):
            await state.update_data(**commit_data)
            return True
        logger.warning("Could not edit applications sort context", exc_info=True)
        try:
            sent = await message.answer(text, reply_markup=markup)
        except TelegramAPIError:
            logger.warning("Could not send applications sort context", exc_info=True)
            return False
        await state.update_data(**{APPLICATIONS_MESSAGE_ID: sent.message_id, **commit_data})
        await _remove_applications_inline_keyboard(message, active_id)
        return True
    await state.update_data(**commit_data)
    return True


async def handle_applications_menu(message: Message, state: FSMContext, api_client: BotApiClient) -> None:
    await state.update_data({
        APPLICATIONS_FILTER_STATUS: None,
        APPLICATIONS_SEARCH_QUERY: None,
        APPLICATIONS_OFFSET: 0,
        APPLICATIONS_SORT: "newest",
    })
    await show_applications_list(message, state, api_client)


async def handle_applications_callback(callback: CallbackQuery, state: FSMContext, api_client: BotApiClient) -> None:
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    message = cast(Message, callback.message)
    state_data = await state.get_data()
    if state_data.get(APPLICATIONS_MESSAGE_ID) != message.message_id:
        return
    view = state_data.get(APPLICATIONS_VIEW)
    data = callback.data or ""
    if data.startswith("applications:search_cancel:"):
        await _handle_search_cancel_callback(message, state, api_client, callback.from_user, state_data, data)
        return
    if data.startswith("applications:next_action"):
        await _handle_next_action_callback(callback, message, state, api_client, state_data)
        return
    if str(view).startswith("next_action_") or await state.get_state() in (
        ApplicationsStates.waiting_for_next_action.state,
        ApplicationsStates.waiting_for_next_action_due_on.state,
    ):
        return
    if data.startswith(("applications:note:", "applications:note_delete:", "applications:note_cancel:")):
        await _handle_note_callback(callback, message, state, api_client, state_data)
        return
    if view in (APPLICATIONS_NOTE_VIEW, "note_loading") or await state.get_state() == ApplicationsStates.waiting_for_note.state:
        return
    if data.startswith("applications:list:"):
        await _handle_list_callback(callback, message, state, api_client, state_data)
        return
    # List controls use one-shot tokens. Legacy controls must not bypass them.
    if data.startswith("applications:open:") or data == "applications:add":
        return
    if data.startswith("applications:page:") and view in (
        APPLICATIONS_LIST_VIEW, APPLICATIONS_FILTER_VIEW, APPLICATIONS_SORT_VIEW,
    ):
        return
    if data.startswith(("applications:status:", "applications:set:", "applications:status_back:")):
        await _handle_status_callback(callback, message, state, api_client, state_data)
        return
    if data.startswith("applications:history:"):
        await _handle_history_callback(callback, message, state, api_client, state_data)
        return
    if data.startswith("applications:match:") and view not in (None, APPLICATIONS_DETAIL_VIEW):
        return
    if data.startswith("applications:detail:") and view not in (
        APPLICATIONS_MATCH_VIEW, APPLICATIONS_HISTORY_VIEW
    ):
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
    if data.startswith("applications:match:"):
        parts = data.split(":")
        if len(parts) == 3:
            app_id, offset = parts[2], (await state.get_data()).get(APPLICATIONS_OFFSET, 0)
        elif len(parts) == 4:
            app_id, offset = parts[2], parts[3]
        else:
            return
        if not app_id.isdecimal() or not str(offset).isdecimal():
            return
        if state_data.get(APPLICATIONS_APPLICATION_ID) not in (None, int(app_id)):
            return
        await _show_application_match(
            message, state, api_client, user_id, int(app_id), int(offset)
        )
        return
    if data.startswith("applications:detail:"):
        parts = data.split(":")
        if len(parts) != 4 or not parts[2].isdecimal() or not parts[3].isdecimal():
            return
        if state_data.get(APPLICATIONS_APPLICATION_ID) != int(parts[2]):
            return
        await _show_application_detail(
            message, state, api_client, user_id, int(parts[2]), int(parts[3])
        )


async def _handle_list_callback(
    callback: CallbackQuery, message: Message, state: FSMContext, api_client: BotApiClient,
    context: dict[str, object],
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) not in (4, 5) or not context.get(APPLICATIONS_LIST_TOKEN):
        return
    if parts[2] != context[APPLICATIONS_LIST_TOKEN]:
        return
    action = parts[3]
    view = context.get(APPLICATIONS_VIEW)
    offset = context.get(APPLICATIONS_OFFSET, 0)
    if type(offset) is not int:
        return
    status = cast(str | None, context.get(APPLICATIONS_FILTER_STATUS))
    q = cast(str | None, context.get(APPLICATIONS_SEARCH_QUERY))
    if action in ("filter", "sort", "add", "open", "page", "search", "reset_search"):
        if view != APPLICATIONS_LIST_VIEW:
            return
        if action in ("filter", "sort", "add", "search", "reset_search"):
            if len(parts) != 4 or (action == "add" and (status is not None or q is not None)):
                return
            if action == "reset_search" and q is None:
                return
        elif len(parts) != 5 or not parts[4].isdecimal():
            return
        elif action == "page" and int(parts[4]) not in (max(0, offset - PAGE_SIZE), offset + PAGE_SIZE):
            return
    elif action in ("set", "back"):
        if view != APPLICATIONS_FILTER_VIEW:
            return
        if action == "set" and (len(parts) != 5 or parts[4] not in ("all", *STATUS_LABELS)):
            return
        if action == "back" and len(parts) != 4:
            return
    elif action in ("sort_set", "sort_cancel"):
        if view != APPLICATIONS_SORT_VIEW:
            return
        if action == "sort_set" and (
            len(parts) != 5 or parts[4] not in SORT_CALLBACK_VALUES
        ):
            return
        if action == "sort_cancel" and len(parts) != 4:
            return
    else:
        return
    if action == "sort":
        token = secrets.token_hex(4)
        text, markup = _sort_picker_content(token, _committed_sort(context))
        await _render_sort_transition(
            message,
            state,
            text,
            markup,
            {APPLICATIONS_VIEW: APPLICATIONS_SORT_VIEW, APPLICATIONS_LIST_TOKEN: token},
        )
        return
    if action in ("sort_set", "sort_cancel"):
        await _handle_sort_choice(
            message,
            state,
            api_client,
            callback.from_user,
            context,
            SORT_CALLBACK_VALUES[parts[4]] if action == "sort_set" else None,
        )
        return
    # Dispatcher event isolation serializes acceptance; consume before any API work.
    await state.update_data({APPLICATIONS_LIST_TOKEN: None})
    if action == "filter":
        token = secrets.token_hex(4)
        choices = {"all": "Все", **STATUS_LABELS}
        rows = [[InlineKeyboardButton(
            text=("✓ " if value == (status or "all") else "") + label,
            callback_data=f"applications:list:{token}:set:{value}",
        )] for value, label in choices.items()]
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"applications:list:{token}:back")])
        await _replace_or_send(message, state, "Показать вакансии:", InlineKeyboardMarkup(inline_keyboard=rows))
        await state.update_data({APPLICATIONS_VIEW: APPLICATIONS_FILTER_VIEW, APPLICATIONS_LIST_TOKEN: token})
        return
    if action == "search":
        token = secrets.token_hex(4)
        await state.set_state(ApplicationsStates.waiting_for_search)
        await state.update_data({APPLICATIONS_VIEW: "search_input", APPLICATIONS_SEARCH_TOKEN: token})
        await _replace_or_send(
            message, state, _search_prompt_text(), _search_prompt_keyboard(token), canonical_target=True,
        )
        return
    if action == "add":
        await remove_active_applications_inline_keyboard(message, state)
        await handle_add_job(message, state)
        return
    if action == "set":
        status = None if parts[4] == "all" else parts[4]
        offset = 0
        await state.update_data({APPLICATIONS_FILTER_STATUS: status, APPLICATIONS_OFFSET: offset})
    elif action == "page":
        offset = int(parts[4])
    elif action == "reset_search":
        offset = 0
    try:
        user_id = await api_client.create_or_get_user(callback.from_user)
    except httpx.HTTPError:
        logger.warning("Could not resolve user for applications list callback", exc_info=True)
        await _replace_or_send(message, state, APPLICATIONS_LOAD_ERROR_MESSAGE, None)
        return
    if action == "open":
        await _show_application_detail(message, state, api_client, user_id, int(parts[4]), offset)
    else:
        await show_applications_list(
            message, state, api_client, offset=offset, user_id=user_id,
            q=None if action == "reset_search" else None,
            commit_search_query=action == "reset_search",
        )


async def handle_sort_cancel(
    message: Message, state: FSMContext, api_client: BotApiClient,
) -> None:
    context = await state.get_data()
    if (
        context.get(APPLICATIONS_VIEW) != APPLICATIONS_SORT_VIEW
        or not isinstance(context.get(APPLICATIONS_LIST_TOKEN), str)
    ):
        return
    await _handle_sort_choice(message, state, api_client, message.from_user, context, None)


async def _handle_sort_choice(
    message: Message,
    state: FSMContext,
    api_client: BotApiClient,
    actor: User | None,
    context: dict[str, object],
    candidate_sort: str | None,
) -> None:
    if actor is None:
        return
    current_sort = _committed_sort(context)
    status = cast(str | None, context.get(APPLICATIONS_FILTER_STATUS))
    q = cast(str | None, context.get(APPLICATIONS_SEARCH_QUERY))
    current_offset = context.get(APPLICATIONS_OFFSET, 0)
    if type(current_offset) is not int:
        return
    target_sort = candidate_sort or current_sort
    target_offset = 0 if candidate_sort is not None else current_offset
    try:
        user_id = await api_client.create_or_get_user(actor)
        page = await api_client.list_applications(
            user_id,
            limit=PAGE_SIZE,
            offset=target_offset,
            status=status,
            q=q,
            sort=target_sort,
        )
        items = cast(list[dict[str, object]], page["items"])
    except httpx.HTTPError:
        logger.warning("Could not load applications for sort transition", exc_info=True)
        token = context.get(APPLICATIONS_LIST_TOKEN)
        if isinstance(token, str):
            text, markup = _sort_picker_content(
                token, current_sort, "Не удалось загрузить вакансии. Попробуй ещё раз.",
            )
            await _render_sort_transition(message, state, text, markup, {})
        return
    token = secrets.token_hex(4)
    text, markup = _applications_list_content(
        items,
        offset=target_offset,
        has_next=bool(page["has_next"]),
        token=token,
        status=status,
        q=q,
        sort=target_sort,
    )
    rendered = await _render_sort_transition(
        message,
        state,
        text,
        markup,
        {
            APPLICATIONS_OFFSET: target_offset,
            APPLICATIONS_VIEW: APPLICATIONS_LIST_VIEW,
            APPLICATIONS_APPLICATION_ID: None,
            APPLICATIONS_LIST_TOKEN: token,
            APPLICATIONS_SEARCH_TOKEN: None,
            APPLICATIONS_SORT: target_sort,
        },
    )
    if rendered:
        await state.set_state(None)


async def handle_search_text(message: Message, state: FSMContext, api_client: BotApiClient) -> None:
    query = (message.text or "").strip()
    if not query:
        await _show_search_input_error(message, state, "Введите непустое название вакансии или компании.")
        return
    if "\u0000" in query or len(query) > 100:
        await _show_search_input_error(message, state, "Поисковый запрос должен содержать от 1 до 100 символов без NUL.")
        return
    context = await state.get_data()
    if await state.get_state() != ApplicationsStates.waiting_for_search.state:
        return
    token = context.get(APPLICATIONS_SEARCH_TOKEN)
    if not isinstance(token, str):
        return
    search_prompt_message_id = context.get(APPLICATIONS_MESSAGE_ID)
    actor = message.from_user
    if actor is None:
        return
    try:
        user_id = await api_client.create_or_get_user(actor)
    except httpx.HTTPError:
        await _show_search_input_error(message, state, "Не удалось выполнить поиск. Попробуй ещё раз.")
        return
    shown = await show_applications_list(
        message, state, api_client, offset=0, user_id=user_id, q=query,
        commit_search_query=True, search_failure_prompt=True,
    )
    if shown and search_prompt_message_id != (await state.get_data()).get(APPLICATIONS_MESSAGE_ID):
        await _delete_application_message(message, search_prompt_message_id)


async def handle_search_non_text(message: Message, state: FSMContext) -> None:
    await _show_search_input_error(message, state, "Отправьте поисковый запрос текстом.")


async def handle_search_cancel(message: Message, state: FSMContext, api_client: BotApiClient) -> None:
    await _cancel_search(message, state, api_client, message.from_user)


async def _handle_search_cancel_callback(
    message: Message, state: FSMContext, api_client: BotApiClient, actor: User | None,
    context: dict[str, object], data: str,
) -> None:
    parts = data.split(":")
    if (
        len(parts) != 3
        or context.get(APPLICATIONS_VIEW) != "search_input"
        or await state.get_state() != ApplicationsStates.waiting_for_search.state
        or parts[2] != context.get(APPLICATIONS_SEARCH_TOKEN)
    ):
        return
    await _cancel_search(message, state, api_client, actor)


async def _cancel_search(
    message: Message, state: FSMContext, api_client: BotApiClient, actor: User | None,
) -> None:
    if actor is None:
        return
    try:
        user_id = await api_client.create_or_get_user(actor)
    except httpx.HTTPError:
        await _show_search_input_error(message, state, "Не удалось загрузить текущий список. Попробуй ещё раз.")
        return
    offset = (await state.get_data()).get(APPLICATIONS_OFFSET, 0)
    if type(offset) is not int:
        return
    await show_applications_list(
        message, state, api_client, offset=offset, user_id=user_id, search_failure_prompt=True,
    )


async def _show_search_input_error(message: Message, state: FSMContext, error: str) -> None:
    token = (await state.get_data()).get(APPLICATIONS_SEARCH_TOKEN)
    if not isinstance(token, str):
        return
    await _replace_or_send(
        message, state, _search_prompt_text(error), _search_prompt_keyboard(token), canonical_target=True,
    )


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
            await _replace_or_send(message, state, APPLICATIONS_LOAD_ERROR_MESSAGE, None)
        return
    except httpx.HTTPError:
        logger.warning("Could not load application detail", exc_info=True)
        await _replace_or_send(message, state, APPLICATIONS_LOAD_ERROR_MESSAGE, None)
        return
    await _render_application_detail(message, state, detail, application_id, offset)


async def _render_application_detail(
    message: Message, state: FSMContext, detail: dict[str, object], application_id: int, offset: int,
) -> None:
    text, markup = _application_detail_content(detail, application_id, offset)
    await state.update_data({APPLICATIONS_STATUS_TOKEN: None, APPLICATIONS_NOTE_TOKEN: None, APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW})
    await _replace_or_send(message, state, text, markup, canonical_target=True)
    await state.update_data(
        **{
            APPLICATIONS_OFFSET: offset,
            APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW,
            APPLICATIONS_APPLICATION_ID: application_id,
        }
    )


def _application_detail_content(
    detail: dict[str, object], application_id: int, offset: int,
) -> tuple[str, InlineKeyboardMarkup]:
    job = detail["job"]
    assert isinstance(job, dict)
    application = detail.get("application")
    status = application.get("status") if isinstance(application, dict) else None
    note = application.get("note") if isinstance(application, dict) else None
    note = note if isinstance(note, str) else None
    suffix = f"\n\nСтатус: {STATUS_LABELS.get(str(status), 'Не указан')}"
    action = application.get("next_action") if isinstance(application, dict) else None
    due_on = application.get("next_action_due_on") if isinstance(application, dict) else None
    if isinstance(action, str) and isinstance(due_on, str):
        suffix += f"\n\n📅 Следующее действие:\n{_display_due_on(due_on)} — {action}"
    if note:
        suffix += f"\n\n📝 Заметка:\n{note}"
    # Count UTF-16 units conservatively, including astral emoji.
    budget = 4096 - len(suffix.encode("utf-16-le")) // 2
    card = format_job_card(job) or "Вакансия без данных."
    if len(card.encode("utf-16-le")) // 2 > budget:
        card = card.encode("utf-16-le")[:max(0, budget - 1) * 2].decode("utf-16-le", errors="ignore") + "…"
    return card + suffix, application_detail_keyboard(application_id, offset, has_note=bool(note), has_next_action=bool(action))


async def _send_new_application_detail(
    message: Message, state: FSMContext, detail: dict[str, object], application_id: int, offset: int,
) -> None:
    text, markup = _application_detail_content(detail, application_id, offset)
    old_message_id = (await state.get_data()).get(APPLICATIONS_MESSAGE_ID)
    await state.update_data(
        **{
            APPLICATIONS_MESSAGE_ID: None,
            APPLICATIONS_OFFSET: offset,
            APPLICATIONS_VIEW: "note_replacing",
            APPLICATIONS_APPLICATION_ID: application_id,
            APPLICATIONS_STATUS_TOKEN: None,
            APPLICATIONS_NOTE_TOKEN: None,
            APPLICATIONS_LIST_TOKEN: None,
        }
    )
    await _delete_application_message(message, old_message_id)
    sent = await message.answer(text, reply_markup=markup, parse_mode=None)
    await state.update_data(
        **{
            APPLICATIONS_MESSAGE_ID: sent.message_id,
            APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW,
        }
    )


async def _show_application_match(
    message: Message,
    state: FSMContext,
    api_client: BotApiClient,
    user_id: int,
    application_id: int,
    offset: int,
) -> None:
    await state.update_data({APPLICATIONS_STATUS_TOKEN: None})
    try:
        match = await api_client.get_application_match(user_id, application_id)
    except httpx.HTTPError:
        logger.warning("Could not get application match", exc_info=True)
        return

    if (await state.get_data()).get(APPLICATIONS_MESSAGE_ID) != message.message_id:
        return
    await remove_active_match_inline_keyboard(message, state)

    text = format_match_message(match)
    await _replace_or_send(
        message,
        state,
        text,
        application_match_keyboard(application_id, offset),
    )
    await state.update_data(
        **{
            APPLICATIONS_OFFSET: offset,
            APPLICATIONS_VIEW: APPLICATIONS_MATCH_VIEW,
            APPLICATIONS_APPLICATION_ID: application_id,
        }
    )


async def _handle_history_callback(
    callback: CallbackQuery,
    message: Message,
    state: FSMContext,
    api_client: BotApiClient,
    context: dict[str, object],
) -> None:
    parts = (callback.data or "").split(":")
    if (
        len(parts) != 4
        or context.get(APPLICATIONS_VIEW) != APPLICATIONS_DETAIL_VIEW
        or not parts[2].isdecimal()
        or not parts[3].isdecimal()
    ):
        return
    application_id, offset = int(parts[2]), int(parts[3])
    if (
        application_id != context.get(APPLICATIONS_APPLICATION_ID)
        or offset != context.get(APPLICATIONS_OFFSET)
    ):
        return
    await state.update_data({APPLICATIONS_VIEW: "history_loading"})
    try:
        user_id = await api_client.create_or_get_user(callback.from_user)
        history = await api_client.get_application_status_history(user_id, application_id)
    except httpx.HTTPError as error:
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 404:
            await state.update_data({
                APPLICATIONS_VIEW: "not_found", APPLICATIONS_APPLICATION_ID: None,
            })
            await _replace_or_send(
                message,
                state,
                APPLICATION_NOT_FOUND_MESSAGE,
                InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="⬅️ К списку", callback_data=f"applications:page:{offset}"
                    )
                ]]),
            )
            return
        logger.warning("Could not load application status history", exc_info=True)
        await state.update_data({APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW})
        try:
            await message.answer("Не удалось загрузить историю статусов. Попробуй ещё раз.")
        except TelegramAPIError:
            logger.warning("Could not send status history error message", exc_info=True)
        return
    items = history.get("items")
    assert isinstance(items, list)
    text = _format_application_status_history(cast(list[dict[str, object]], items))
    await state.update_data({APPLICATIONS_VIEW: APPLICATIONS_HISTORY_VIEW})
    await _replace_or_send(
        message, state, text, application_history_keyboard(application_id, offset)
    )


def _format_application_status_history(items: list[dict[str, object]]) -> str:
    header = "🕘 История статусов"
    if not items:
        return f"{header}\n\nИстория статусов пока пуста."
    lines = [
        f"{_history_timestamp(item['occurred_at'])} — "
        f"{STATUS_LABELS.get(str(item.get('status')), 'Не указан')}"
        for item in items
    ]
    full = f"{header}\n\n" + "\n".join(lines)
    if _utf16_units(full) <= 4096:
        return full
    notice = "Показана только часть истории."
    visible: list[str] = []
    for line in lines:
        candidate = f"{header}\n\n" + "\n".join([*visible, line]) + f"\n\n{notice}"
        if _utf16_units(candidate) > 4096:
            break
        visible.append(line)
    return f"{header}\n\n" + "\n".join(visible) + f"\n\n{notice}"


def _history_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Status history timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Status history timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


async def remove_active_applications_inline_keyboard(message: Message, state: FSMContext) -> None:
    await state.update_data({APPLICATIONS_NEXT_ACTION_TOKEN: None, APPLICATIONS_NEXT_ACTION_DRAFT: None})
    await state.update_data({APPLICATIONS_STATUS_TOKEN: None, APPLICATIONS_LIST_TOKEN: None, APPLICATIONS_NOTE_TOKEN: None})
    message_id = (await state.get_data()).get(APPLICATIONS_MESSAGE_ID)
    await _remove_applications_inline_keyboard(message, message_id)


async def _handle_status_callback(
    callback: CallbackQuery, message: Message, state: FSMContext, api_client: BotApiClient,
    context: dict[str, object],
) -> None:
    parts = (callback.data or "").split(":")
    action = parts[1]
    opening = action == "status"
    if opening:
        if len(parts) != 4 or context.get(APPLICATIONS_VIEW) != APPLICATIONS_DETAIL_VIEW:
            return
        if not parts[2].isdecimal() or not parts[3].isdecimal():
            return
        if int(parts[2]) != context.get(APPLICATIONS_APPLICATION_ID) or int(parts[3]) != context.get(APPLICATIONS_OFFSET):
            return
    else:
        if len(parts) != (4 if action == "set" else 3):
            return
        if context.get(APPLICATIONS_VIEW) != APPLICATIONS_STATUS_VIEW or parts[2] != context.get(APPLICATIONS_STATUS_TOKEN):
            return
        if action == "set" and parts[3] not in STATUS_LABELS:
            return
    application_id = context.get(APPLICATIONS_APPLICATION_ID)
    offset = context.get(APPLICATIONS_OFFSET)
    if not isinstance(application_id, int) or not isinstance(offset, int):
        return
    if not opening:
        await state.update_data({APPLICATIONS_STATUS_TOKEN: None, APPLICATIONS_VIEW: "status_loading"})
    try:
        user_id = await api_client.create_or_get_user(callback.from_user)
        if action == "set":
            detail = await api_client.put_application_status(user_id, application_id, parts[3])
        else:
            detail = await api_client.get_application(user_id, application_id)
    except httpx.HTTPError as error:
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 404:
            await state.update_data({
                APPLICATIONS_STATUS_TOKEN: None,
                APPLICATIONS_VIEW: "not_found",
                APPLICATIONS_APPLICATION_ID: None,
            })
            await _replace_or_send(
                message, state, APPLICATION_NOT_FOUND_MESSAGE,
                InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ К списку", callback_data=f"applications:page:{offset}")
                ]]),
            )
            return
        logger.warning("Could not load or update application status", exc_info=True)
        # A timeout may follow a committed write. Do not claim that DB is unchanged.
        await state.update_data({APPLICATIONS_STATUS_TOKEN: None, APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW})
        await _replace_or_send(
            message, state, "Не удалось подтвердить статус. Открой выбор статуса заново, чтобы проверить актуальные данные.",
            application_detail_keyboard(application_id, offset),
        )
        return
    if not opening:
        await _render_application_detail(message, state, detail, application_id, offset)
        return
    application = detail.get("application")
    current = application.get("status") if isinstance(application, dict) else None
    token = secrets.token_hex(4)
    rows = [[InlineKeyboardButton(
        text=("✓ " if status == current else "") + label,
        callback_data=f"applications:set:{token}:{status}",
    )] for status, label in STATUS_LABELS.items()]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"applications:status_back:{token}")])
    await state.update_data({APPLICATIONS_VIEW: APPLICATIONS_STATUS_VIEW, APPLICATIONS_STATUS_TOKEN: token})
    await _replace_or_send(message, state, "Выбери статус вакансии:", InlineKeyboardMarkup(inline_keyboard=rows))


def _display_due_on(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{parsed.day:02d}.{parsed.month:02d}.{parsed.year:04d}"


def _next_action_keyboard(token: str, *, recovery: bool = False) -> InlineKeyboardMarkup:
    action = "refresh" if recovery else "cancel"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="К актуальной вакансии" if recovery else "Отмена",
            callback_data=f"applications:next_action_{action}:{token}",
        )
    ]])


def _next_action_due_prompt(*, invalid: bool = False, non_text: bool = False) -> str:
    text = (
        "Когда это сделать?\n\n"
        "Отправь дату в формате ДД.ММ.ГГГГ, например: 12.09.2026.\n"
        "Пока можно указать только дату, без времени."
    )
    if invalid:
        return text + "\n\n⚠️ Дата некорректна. Попробуй ещё раз."
    if non_text:
        return text + "\n\n⚠️ Отправь дату текстом."
    return text


async def _send_next_action_due_prompt(
    message: Message, state: FSMContext, action: str, token: str,
) -> bool:
    old_message_id = (await state.get_data()).get(APPLICATIONS_MESSAGE_ID)
    try:
        sent = await message.answer(
            _next_action_due_prompt(), reply_markup=_next_action_keyboard(token)
        )
    except TelegramAPIError:
        logger.warning("Could not send next action due prompt", exc_info=True)
        return False
    await state.set_state(ApplicationsStates.waiting_for_next_action_due_on)
    await state.update_data({
        APPLICATIONS_MESSAGE_ID: sent.message_id,
        APPLICATIONS_NEXT_ACTION_DRAFT: action,
        APPLICATIONS_NEXT_ACTION_TOKEN: token,
        APPLICATIONS_VIEW: "next_action_due_input",
    })
    await _delete_application_message(message, old_message_id)
    return True


async def handle_next_action_non_text(message: Message, state: FSMContext) -> None:
    if await state.get_state() != ApplicationsStates.waiting_for_next_action_due_on.state:
        await message.answer("Отправь текст действия или нажми Отмена.")
        return
    token = (await state.get_data()).get(APPLICATIONS_NEXT_ACTION_TOKEN)
    if not isinstance(token, str):
        return
    await _replace_or_send(
        message,
        state,
        _next_action_due_prompt(non_text=True),
        _next_action_keyboard(token),
        canonical_target=True,
    )


async def _handle_next_action_callback(
    callback: CallbackQuery, message: Message, state: FSMContext,
    api_client: BotApiClient, context: dict[str, object],
) -> None:
    parts = (callback.data or "").split(":")
    action = parts[1]
    if action in ("next_action_cancel", "next_action_refresh"):
        if len(parts) != 3 or not context.get(APPLICATIONS_NEXT_ACTION_TOKEN) or parts[2] != context[APPLICATIONS_NEXT_ACTION_TOKEN]:
            return
        expected = ("next_action_input", "next_action_due_input") if action == "next_action_cancel" else ("next_action_error",)
        if context.get(APPLICATIONS_VIEW) not in expected:
            return
    elif action in ("next_action", "next_action_delete"):
        if (len(parts) != 4 or context.get(APPLICATIONS_VIEW) != APPLICATIONS_DETAIL_VIEW
                or not parts[2].isdecimal() or not parts[3].isdecimal()
                or int(parts[2]) != context.get(APPLICATIONS_APPLICATION_ID)
                or int(parts[3]) != context.get(APPLICATIONS_OFFSET)):
            return
    else:
        return
    await _next_action_operation(message, state, api_client, callback.from_user, action)


async def handle_next_action_cancel(message: Message, state: FSMContext, api_client: BotApiClient) -> None:
    await _next_action_operation(message, state, api_client, message.from_user, "next_action_cancel")


async def handle_next_action_text(message: Message, state: FSMContext, api_client: BotApiClient) -> None:
    text = (message.text or "").strip()
    if await state.get_state() == ApplicationsStates.waiting_for_next_action.state:
        if not 1 <= len(text) <= 500 or "\u0000" in text:
            await message.answer("Действие должно содержать от 1 до 500 символов текста.")
            return
        await _send_next_action_due_prompt(message, state, text, secrets.token_hex(4))
        return
    try:
        if re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", text) is None:
            raise ValueError
        day, month, year = map(int, text.split("."))
        due_on = date(year, month, day).isoformat()
    except ValueError:
        token = (await state.get_data()).get(APPLICATIONS_NEXT_ACTION_TOKEN)
        if not isinstance(token, str):
            return
        await _replace_or_send(
            message,
            state,
            _next_action_due_prompt(invalid=True),
            _next_action_keyboard(token),
            canonical_target=True,
        )
        return
    await _next_action_operation(message, state, api_client, message.from_user, "save", due_on)


async def _next_action_operation(
    message: Message, state: FSMContext, api_client: BotApiClient, actor: User | None,
    action: str, due_on: str | None = None,
) -> None:
    context = await state.get_data()
    application_id, offset = context.get(APPLICATIONS_APPLICATION_ID), context.get(APPLICATIONS_OFFSET)
    draft = context.get(APPLICATIONS_NEXT_ACTION_DRAFT)
    if actor is None or type(application_id) is not int or type(offset) is not int:
        return
    await state.set_state(None)
    await state.update_data({APPLICATIONS_VIEW: "next_action_loading", APPLICATIONS_NEXT_ACTION_TOKEN: None,
                             APPLICATIONS_NEXT_ACTION_DRAFT: None, APPLICATIONS_STATUS_TOKEN: None,
                             APPLICATIONS_NOTE_TOKEN: None, APPLICATIONS_LIST_TOKEN: None})
    try:
        user_id = await api_client.create_or_get_user(actor)
        if action == "save":
            assert isinstance(draft, str) and due_on is not None
            detail = await api_client.set_application_next_action(user_id, application_id, draft, due_on)
        elif action == "next_action_delete":
            detail = await api_client.delete_application_next_action(user_id, application_id)
        else:
            detail = await api_client.get_application(user_id, application_id)
    except httpx.HTTPError as error:
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 404:
            await state.update_data({APPLICATIONS_VIEW: "not_found", APPLICATIONS_APPLICATION_ID: None})
            await _replace_or_send(message, state, APPLICATION_NOT_FOUND_MESSAGE,
                InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                    text="⬅️ К списку", callback_data=f"applications:page:{offset}")]]), canonical_target=True)
            return
        logger.warning("Could not confirm application next action operation", exc_info=True)
        if action == "next_action":
            await state.update_data({APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW})
            try:
                await message.answer("Не удалось загрузить следующее действие. Попробуй ещё раз.")
            except TelegramAPIError:
                logger.warning("Could not send next action load error", exc_info=True)
            return
        token = secrets.token_hex(4)
        await state.update_data({APPLICATIONS_VIEW: "next_action_error", APPLICATIONS_NEXT_ACTION_TOKEN: token})
        await _replace_or_send(message, state,
            "Не удалось подтвердить актуальные данные. Открой вакансию, чтобы проверить следующее действие.",
            _next_action_keyboard(token, recovery=True), canonical_target=True)
        return
    if action == "next_action":
        application = detail["application"]
        assert isinstance(application, dict)
        current, current_date = application.get("next_action"), application.get("next_action_due_on")
        text = "Что нужно сделать дальше?\n\nНапример: «Написать HR»"
        if isinstance(current, str) and isinstance(current_date, str):
            text = (f"📅 Текущее следующее действие:\n{_display_due_on(current_date)} — {current}\n\n"
                    "Отправь новое действие.\n\n⚠️ Текущее действие и дата будут полностью заменены.")
        token = secrets.token_hex(4)
        await state.set_state(ApplicationsStates.waiting_for_next_action)
        await state.update_data({APPLICATIONS_VIEW: "next_action_input", APPLICATIONS_NEXT_ACTION_TOKEN: token})
        await _replace_or_send(message, state, text, _next_action_keyboard(token), canonical_target=True)
    elif action in ("save", "next_action_delete"):
        try:
            await _send_new_application_detail(message, state, detail, application_id, offset)
        except TelegramAPIError:
            await state.update_data({APPLICATIONS_VIEW: "next_action_render_failed"})
            logger.warning("Next action persisted but Telegram delivery failed", exc_info=True)
    else:
        await _render_application_detail(message, state, detail, application_id, offset)


def _note_cancel_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отмена", callback_data=f"applications:note_cancel:{token}")
    ]])


async def _handle_note_callback(
    callback: CallbackQuery, message: Message, state: FSMContext,
    api_client: BotApiClient, context: dict[str, object],
) -> None:
    parts = (callback.data or "").split(":")
    action = parts[1]
    if action == "note_cancel":
        if (len(parts) != 3 or not context.get(APPLICATIONS_NOTE_TOKEN)
                or parts[2] != context[APPLICATIONS_NOTE_TOKEN]
                or await state.get_state() != ApplicationsStates.waiting_for_note.state):
            return
    elif (len(parts) != 4 or context.get(APPLICATIONS_VIEW) != APPLICATIONS_DETAIL_VIEW
          or not parts[2].isdecimal() or not parts[3].isdecimal()
          or int(parts[2]) != context.get(APPLICATIONS_APPLICATION_ID)
          or int(parts[3]) != context.get(APPLICATIONS_OFFSET)):
        return
    await _note_action(message, state, api_client, callback.from_user, action)


async def handle_note_cancel(message: Message, state: FSMContext, api_client: BotApiClient) -> None:
    await _note_action(message, state, api_client, message.from_user, "note_cancel")


async def handle_note_text(message: Message, state: FSMContext, api_client: BotApiClient) -> None:
    note = (message.text or "").strip()
    if not 1 <= len(note) <= 1000 or "\u0000" in note:
        await message.answer("Заметка должна содержать от 1 до 1000 символов текста.")
        return
    await _note_action(message, state, api_client, message.from_user, "save", note)


async def _note_action(
    message: Message, state: FSMContext, api_client: BotApiClient,
    actor: User | None, action: str, note: str | None = None,
) -> None:
    context = await state.get_data()
    application_id, offset = context.get(APPLICATIONS_APPLICATION_ID), context.get(APPLICATIONS_OFFSET)
    if actor is None or type(application_id) is not int or type(offset) is not int:
        return
    # Event isolation serializes text/callback updates. Claim input before I/O.
    await state.set_state(None)
    await state.update_data({APPLICATIONS_VIEW: "note_loading", APPLICATIONS_NOTE_TOKEN: None,
                             APPLICATIONS_STATUS_TOKEN: None, APPLICATIONS_LIST_TOKEN: None})
    try:
        user_id = await api_client.create_or_get_user(actor)
        if action == "save":
            assert note is not None
            detail = await api_client.put_application_note(user_id, application_id, note)
        elif action == "note_delete":
            detail = await api_client.delete_application_note(user_id, application_id)
        else:
            detail = await api_client.get_application(user_id, application_id)
    except httpx.HTTPError as error:
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 404:
            await state.update_data({APPLICATIONS_VIEW: "not_found", APPLICATIONS_APPLICATION_ID: None})
            await _replace_or_send(message, state, APPLICATION_NOT_FOUND_MESSAGE,
                InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ К списку", callback_data=f"applications:page:{offset}")
                ]]), canonical_target=True)
            return
        if action in ("note", "note_delete"):
            logger.warning("Could not load or delete application note", exc_info=True)
            await state.update_data({APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW})
            error_text = (
                "Не удалось загрузить заметку. Попробуй ещё раз."
                if action == "note"
                else "Не удалось подтвердить удаление заметки. Повтори удаление или заново открой вакансию, чтобы проверить актуальные данные."
            )
            try:
                await message.answer(error_text)
            except TelegramAPIError:
                logger.warning("Could not send application note error message", exc_info=True)
            return
        logger.warning("Could not confirm application note operation", exc_info=True)
        token = secrets.token_hex(4)
        await state.set_state(ApplicationsStates.waiting_for_note)
        await state.update_data({APPLICATIONS_VIEW: APPLICATIONS_NOTE_VIEW, APPLICATIONS_NOTE_TOKEN: token})
        await _replace_or_send(message, state,
            "Не удалось подтвердить данные заметки. Отправь текст ещё раз или нажми Отмена, чтобы загрузить актуальную вакансию.",
            _note_cancel_keyboard(token), canonical_target=True)
        return
    if action == "note":
        application = detail["application"]
        assert isinstance(application, dict)
        current = application.get("note")
        if current:
            text = (
                f"📝 Текущая заметка:\n\n{current}\n\n"
                "Отправь новый текст заметки (1–1000 символов).\n\n"
                "⚠️ Старая заметка будет полностью заменена."
            )
        else:
            text = "Отправь текст заметки (1–1000 символов)."
        token = secrets.token_hex(4)
        await state.set_state(ApplicationsStates.waiting_for_note)
        await state.update_data({APPLICATIONS_VIEW: APPLICATIONS_NOTE_VIEW, APPLICATIONS_NOTE_TOKEN: token})
        await _replace_or_send(message, state, text, _note_cancel_keyboard(token), canonical_target=True)
    elif action in ("save", "note_delete"):
        try:
            await _send_new_application_detail(message, state, detail, application_id, offset)
        except TelegramAPIError:
            # The API write is complete; stale prompt controls must stay inert.
            await state.update_data({APPLICATIONS_VIEW: "note_render_failed"})
            logger.warning("Application note saved/loaded but Telegram delivery failed", exc_info=True)
    else:
        await _render_application_detail(message, state, detail, application_id, offset)


async def _delete_application_message(message: Message, message_id: object) -> None:
    if not isinstance(message_id, int) or message.bot is None:
        return
    try:
        await message.bot.delete_message(chat_id=message.chat.id, message_id=message_id)
    except TelegramAPIError:
        logger.warning("Could not delete replaced applications message", exc_info=True)
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
