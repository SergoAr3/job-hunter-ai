"""Telegram views for ephemeral Discover search and explicit Save."""

import logging
import secrets
from html import escape
from typing import cast

import httpx
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from app.api_client import BotApiClient
from app.applications import handle_applications_menu
from app.telegram_cleanup import is_message_not_modified

logger = logging.getLogger(__name__)

DISCOVER_BUTTON = "🔎 Найти вакансии"
PAGE_SIZE = 5
_PREFIX = "discover_"
_PROMPT = "Что ищем?\nНапример: Python backend"
_STALE = "Эта кнопка уже неактуальна."
DESCRIPTION_PREVIEW_LIMIT = 360
REQUIREMENTS_PREVIEW_LIMIT = 280


class DiscoverStates(StatesGroup):
    waiting_for_query = State()


def _token() -> str:
    return secrets.token_hex(4)


def _button(text: str, token: str, action: str, index: int | None = None) -> InlineKeyboardButton:
    suffix = f":{index}" if index is not None else ""
    return InlineKeyboardButton(text=text, callback_data=f"discover:{token}:{action}{suffix}")


def _short(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    boundary = value.rfind(" ", 0, limit)
    if boundary < limit // 2:
        boundary = limit - 1
    return value[:boundary].rstrip() + "…"


def _escaped_preview(value: object, limit: int) -> str:
    return escape(_short(value, limit))


def format_location(value: object) -> str:
    if not isinstance(value, str):
        return ""
    segments = [segment.strip() for segment in value.split(",") if segment.strip()]
    return ", ".join(segments[:2])


def _loading_text(query: str) -> str:
    return f"🔎 Ищу вакансии по запросу «{escape(_short(query, 80))}»…"


def format_salary(item: dict[str, object]) -> str:
    minimum = item.get("salary_min")
    maximum = item.get("salary_max")
    minimum = minimum if type(minimum) in (int, float) and minimum > 0 else None
    maximum = maximum if type(maximum) in (int, float) and maximum > 0 else None
    currency = " ₽" if item.get("salary_currency") == "RUB" else (
        f" {item['salary_currency']}" if isinstance(item.get("salary_currency"), str) else ""
    )
    if minimum is not None and maximum is not None:
        if minimum == maximum:
            return f"{minimum:g}{currency}"
        return f"{minimum:g}–{maximum:g}{currency}"
    if minimum is not None:
        return f"от {minimum:g}{currency}"
    if maximum is not None:
        return f"до {maximum:g}{currency}"
    return "Зарплата не указана"


def format_preview(preview: dict[str, object]) -> str:
    if not preview.get("available"):
        if preview.get("unavailable_reason") == "profile_missing_for_preview":
            return "🎯 Matching\nЧтобы показывать Matching, заполните профиль."
        return "🎯 Matching\nПока недоступен."
    if preview.get("verdict") == "insufficient_data" or preview.get("score") is None:
        return "🎯 Matching\nПока недостаточно данных для надёжной оценки."
    labels = {
        "high": "Хорошее совпадение",
        "medium": "Частичное совпадение",
        "low": "Слабое совпадение",
    }
    label = labels.get(preview.get("verdict"), "Оценка по профилю")
    return f"🎯 Matching: {preview['score']}/100\n{label}"


def _list_content(data: dict[str, object], token: str) -> tuple[str, InlineKeyboardMarkup]:
    items = cast(list[dict[str, object]], data["discover_current_items"])
    rows = [[_button(f"{index + 1}. {_short(item['title'], 36)}", token, "open", index)]
            for index, item in enumerate(items)]
    nav = []
    if data["discover_offset"] > 0:
        nav.append(_button("⬅️ Назад", token, "prev"))
    if data["discover_next_offset"] is not None:
        nav.append(_button("➡️ Далее", token, "next"))
    if nav:
        rows.append(nav)
    rows.extend([[_button("✏️ Изменить запрос", token, "query")],
                 [_button("🏠 Меню", token, "menu")]])
    if not items:
        text = "По этому запросу ничего не нашлось."
    else:
        text = "🔎 Найденные вакансии\n\n" + "\n\n".join(
            f"{index + 1}. {_short(item['title'], 100)}\n   {_short(item['company'], 70)}"
            for index, item in enumerate(items)
        ) + "\n\nИсточник: Работа России"
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _detail_content(item: dict[str, object], token: str, *, has_next: bool) -> tuple[str, InlineKeyboardMarkup]:
    parts = [_escaped_preview(item["title"], 180), _escaped_preview(item["company"], 100)]
    location = format_location(item.get("location"))
    if location:
        parts.append("📍 " + _escaped_preview(location, 160))
    if item.get("workplace_type") == "remote":
        parts.append("🌐 Удалённо")
    parts.append("💰 " + format_salary(item))
    if item.get("description"):
        parts.append("Описание:\n" + _escaped_preview(item["description"], DESCRIPTION_PREVIEW_LIMIT))
    if item.get("requirements_text"):
        parts.append("Требования:\n" + _escaped_preview(
            item["requirements_text"], REQUIREMENTS_PREVIEW_LIMIT
        ))
    parts.extend([format_preview(cast(dict[str, object], item["preview_match"])),
                  "Источник: Работа России"])
    rows = []
    if item["already_saved_for_user"]:
        label = "✅ В моих вакансиях" if item.get("_saved_now") else "✅ Уже в моих вакансиях"
        rows.append([_button(label, token, "saved")])
    else:
        rows.append([_button("💾 Сохранить", token, "save")])
    if has_next:
        rows.append([_button("➡️ Следующая", token, "advance")])
    rows.append([_button("⬅️ К списку", token, "list")])
    if item["already_saved_for_user"]:
        rows.append([_button("📂 Мои вакансии", token, "applications")])
    rows.append([InlineKeyboardButton(text="🔗 Открыть вакансию", url=cast(str, item["source_url"]))])
    return "\n\n".join(parts), InlineKeyboardMarkup(inline_keyboard=rows)


def _error_text(error: httpx.HTTPError, *, saving: bool) -> str:
    code = None
    if isinstance(error, httpx.HTTPStatusError):
        try:
            detail = error.response.json().get("detail", {})
            code = detail.get("code") if isinstance(detail, dict) else None
        except (ValueError, AttributeError):
            pass
    return {
        "source_timeout": "Источник вакансий отвечает слишком долго. Попробуйте ещё раз.",
        "source_unavailable": "Источник вакансий временно недоступен. Попробуйте чуть позже.",
        "source_rate_limited": "Источник вакансий временно недоступен. Попробуйте чуть позже.",
        "source_bad_response": "Не удалось получить вакансии. Попробуйте повторить поиск.",
        "vacancy_not_found": "Эта вакансия уже недоступна. Обновите результаты поиска.",
        "source_identity_conflict": "Не удалось безопасно сохранить вакансию. Обновите поиск и попробуйте снова.",
    }.get(code, "Не удалось сохранить вакансию. Попробуйте ещё раз." if saving
          else "Не удалось получить вакансии. Попробуйте повторить поиск.")


async def _remove_keyboard(message: Message, message_id: object) -> None:
    if not isinstance(message_id, int) or message.bot is None:
        return
    try:
        await message.bot.edit_message_reply_markup(
            chat_id=message.chat.id, message_id=message_id, reply_markup=None
        )
    except TelegramBadRequest as error:
        if not is_message_not_modified(error):
            logger.warning("Could not remove Discover keyboard", exc_info=True)
    except TelegramAPIError:
        logger.warning("Could not remove Discover keyboard", exc_info=True)


async def remove_active_discover_inline_keyboard(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(discover_screen_token=None)
    await _remove_keyboard(message, data.get("discover_active_message_id"))


async def _render(message: Message, state: FSMContext, text: str,
                  markup: InlineKeyboardMarkup | None, *, screen: str, token: str) -> None:
    active_id = (await state.get_data()).get("discover_active_message_id")
    if isinstance(active_id, int) and message.bot is not None:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id, message_id=active_id, text=text,
                reply_markup=markup, parse_mode=ParseMode.HTML if screen == "detail" else None,
            )
            await state.update_data(discover_current_screen=screen, discover_screen_token=token)
            return
        except TelegramAPIError as error:
            if is_message_not_modified(error):
                await state.update_data(discover_current_screen=screen, discover_screen_token=token)
                return
            logger.warning("Could not edit Discover card", exc_info=True)
            await _remove_keyboard(message, active_id)
    sent = await message.answer(
        text, reply_markup=markup, parse_mode=ParseMode.HTML if screen == "detail" else None
    )
    await state.update_data(
        discover_active_message_id=sent.message_id,
        discover_current_screen=screen,
        discover_screen_token=token,
    )


async def start_discover(message: Message, state: FSMContext) -> None:
    await state.set_state(DiscoverStates.waiting_for_query)
    sent = await message.answer(_PROMPT, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [_button("🏠 Меню", token := _token(), "menu")],
    ]))
    await state.update_data(
        discover_query=None, discover_market_country="RU", discover_offset=0,
        discover_next_offset=None, discover_current_items=[], discover_selected_index=None,
        discover_current_screen="query", discover_active_message_id=sent.message_id,
        discover_screen_token=token,
    )


async def _show_page(message: Message, state: FSMContext, api_client: BotApiClient,
                     *, offset: int, user=None) -> None:
    data = await state.get_data()
    await state.update_data(
        discover_screen_token=None, discover_current_screen="loading", discover_offset=offset
    )
    try:
        if user is None:
            user = message.from_user
        if user is None:
            return
        page = await _discover_page_with_typing(
            message,
            api_client,
            user,
            query=cast(str, data["discover_query"]),
            offset=offset,
        )
    except httpx.HTTPError as error:
        logger.warning("Discover search failed", exc_info=True)
        await _show_error(message, state, _error_text(error, saving=False), saving=False)
        return
    await state.update_data(
        discover_offset=offset, discover_next_offset=page["next_offset"],
        discover_current_items=page["items"], discover_selected_index=None,
    )
    data = await state.get_data()
    token = _token()
    text, markup = _list_content(data, token)
    await _render(message, state, text, markup, screen="list", token=token)
    await state.set_state(None)


async def _discover_page_with_typing(
    message: Message,
    api_client: BotApiClient,
    user: object,
    *,
    query: str,
    offset: int,
) -> dict[str, object]:
    sender: ChatActionSender | None = None
    try:
        if message.bot is None:
            raise RuntimeError("Message is not bound to a bot")
        sender = ChatActionSender.typing(chat_id=message.chat.id, bot=message.bot)
        await sender.__aenter__()
    except Exception:
        sender = None
        logger.warning("Could not start Telegram typing indicator", exc_info=True)

    try:
        user_id = await api_client.create_or_get_user(user)
        return await api_client.discover_jobs(user_id, query=query, limit=PAGE_SIZE, offset=offset)
    finally:
        if sender is not None:
            try:
                await sender.__aexit__(None, None, None)
            except Exception:
                logger.warning("Could not stop Telegram typing indicator", exc_info=True)


async def handle_discover_query(message: Message, state: FSMContext,
                                api_client: BotApiClient) -> None:
    query = (message.text or "").strip()
    if not query:
        await _render(message, state, "Введите поисковый запрос.\n" + _PROMPT,
                      InlineKeyboardMarkup(inline_keyboard=[
                          [_button("🏠 Меню", token := _token(), "menu")]
                      ]), screen="query", token=token)
        return
    if len(query) > 100:
        await _render(message, state, "Запрос слишком длинный. До 100 символов.\n" + _PROMPT,
                      InlineKeyboardMarkup(inline_keyboard=[
                          [_button("🏠 Меню", token := _token(), "menu")]
                      ]), screen="query", token=token)
        return
    previous_message_id = (await state.get_data()).get("discover_active_message_id")
    await state.update_data(
        discover_query=query,
        discover_screen_token=None,
        discover_current_screen="loading",
        discover_offset=0,
        discover_next_offset=None,
        discover_current_items=[],
        discover_selected_index=None,
    )
    await _remove_keyboard(message, previous_message_id)
    loading = await message.answer(_loading_text(query), parse_mode=ParseMode.HTML)
    await state.update_data(discover_active_message_id=loading.message_id)
    await state.set_state(None)
    await _show_page(message, state, api_client, offset=0)


async def handle_discover_non_text(message: Message, state: FSMContext) -> None:
    await _render(message, state, "Отправьте текстовый запрос.\n" + _PROMPT,
                  InlineKeyboardMarkup(inline_keyboard=[
                      [_button("🏠 Меню", token := _token(), "menu")]
                  ]), screen="query", token=token)


async def _show_detail(message: Message, state: FSMContext, index: int) -> None:
    data = await state.get_data()
    items = cast(list[dict[str, object]], data["discover_current_items"])
    await state.update_data(discover_selected_index=index)
    token = _token()
    text, markup = _detail_content(items[index], token, has_next=(
        index + 1 < len(items) or data["discover_next_offset"] is not None
    ))
    await _render(message, state, text, markup, screen="detail", token=token)


async def _show_error(message: Message, state: FSMContext, text: str, *, saving: bool) -> None:
    token = _token()
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [_button("🔄 Повторить", token, "retry_save" if saving else "retry")],
        [_button("✏️ Изменить запрос", token, "query")],
        [_button("🏠 Меню", token, "menu")],
    ])
    await _render(message, state, text, markup, screen="save_error" if saving else "search_error", token=token)


async def _save(message: Message, state: FSMContext, api_client: BotApiClient, user) -> None:
    data = await state.get_data()
    index = data.get("discover_selected_index")
    items = data.get("discover_current_items")
    if not isinstance(index, int) or not isinstance(items, list) or index >= len(items):
        return
    item = items[index]
    if item["already_saved_for_user"]:
        return
    await state.update_data(discover_screen_token=None, discover_current_screen="saving")
    try:
        user_id = await api_client.create_or_get_user(user)
        result = await api_client.save_discovered_job(
            user_id, source=cast(str, item["source"]),
            source_scope=cast(str, item["source_scope"]),
            external_id=cast(str, item["external_id"]),
        )
    except httpx.HTTPError as error:
        logger.warning("Discover save failed", exc_info=True)
        await _show_error(message, state, _error_text(error, saving=True), saving=True)
        return
    updated = dict(item, already_saved_for_user=True, _saved_now=result["application_created"])
    items = list(items)
    items[index] = updated
    await state.update_data(discover_current_items=items)
    await _show_detail(message, state, index)
    if not result["application_created"]:
        # The saved state is accurate even when an existing CRM status has advanced.
        logger.info("Discover application already existed for user")


async def handle_discover_callback(callback: CallbackQuery, state: FSMContext,
                                   api_client: BotApiClient) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer(_STALE)
        return
    message = cast(Message, callback.message)
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    if (len(parts) not in (3, 4) or parts[0] != "discover"
            or parts[1] != data.get("discover_screen_token")
            or message.message_id != data.get("discover_active_message_id")):
        await callback.answer(_STALE)
        return
    action = parts[2]
    screen = data.get("discover_current_screen")
    items = data.get("discover_current_items")
    index = data.get("discover_selected_index")
    allowed = {
        "query": {"menu"},
        "list": {"open", "prev", "next", "query", "menu"},
        "detail": {"save", "saved", "advance", "list", "applications"},
        "search_error": {"retry", "query", "menu"},
        "save_error": {"retry_save", "query", "menu"},
    }
    if action not in allowed.get(screen, set()):
        await callback.answer(_STALE)
        return
    if action == "open":
        if (len(parts) != 4 or not parts[3].isdecimal() or not isinstance(items, list)
                or int(parts[3]) >= len(items)):
            await callback.answer(_STALE)
            return
    elif len(parts) != 3:
        await callback.answer(_STALE)
        return
    if action in {"save", "saved", "advance", "list", "applications", "retry_save"}:
        if not isinstance(index, int) or not isinstance(items, list) or index >= len(items):
            await callback.answer(_STALE)
            return
    if action == "save" and items[index]["already_saved_for_user"]:
        await callback.answer(_STALE)
        return
    await callback.answer()
    if action == "open":
        await _show_detail(message, state, int(parts[3]))
    elif action == "list":
        token = _token()
        text, markup = _list_content(data, token)
        await _render(message, state, text, markup, screen="list", token=token)
    elif action == "advance":
        if index + 1 < len(items):
            await _show_detail(message, state, index + 1)
        elif data.get("discover_next_offset") is not None:
            await _show_page(message, state, api_client,
                             offset=cast(int, data["discover_next_offset"]), user=callback.from_user)
            new_data = await state.get_data()
            if new_data.get("discover_current_screen") == "list" and new_data.get("discover_current_items"):
                await _show_detail(message, state, 0)
    elif action == "next" and data.get("discover_next_offset") is not None:
        await _show_page(message, state, api_client,
                         offset=cast(int, data["discover_next_offset"]), user=callback.from_user)
    elif action == "prev" and isinstance(data.get("discover_offset"), int) and data["discover_offset"] > 0:
        await _show_page(message, state, api_client,
                         offset=max(0, data["discover_offset"] - PAGE_SIZE), user=callback.from_user)
    elif action == "retry":
        await _show_page(message, state, api_client,
                         offset=cast(int, data["discover_offset"]), user=callback.from_user)
    elif action == "save" or action == "retry_save":
        await _save(message, state, api_client, callback.from_user)
    elif action == "query":
        token = _token()
        await _render(message, state, _PROMPT, InlineKeyboardMarkup(inline_keyboard=[
            [_button("🏠 Меню", token, "menu")]
        ]), screen="query", token=token)
        await state.set_state(DiscoverStates.waiting_for_query)
    elif action == "applications":
        await remove_active_discover_inline_keyboard(message, state)
        await state.clear()
        await handle_applications_menu(message, state, api_client, actor=callback.from_user)
    elif action == "menu":
        await remove_active_discover_inline_keyboard(message, state)
        await state.clear()
        from app.menu import main_menu_keyboard
        await message.answer("Главное меню", reply_markup=main_menu_keyboard())


async def handle_discover_cancel(message: Message, state: FSMContext) -> None:
    await remove_active_discover_inline_keyboard(message, state)
    await state.clear()
    from app.menu import main_menu_keyboard
    await message.answer("Поиск отменён.", reply_markup=main_menu_keyboard())
