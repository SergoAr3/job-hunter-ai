"""Telegram work history editor and optional CV additions. Persistence is API-only."""
import re
import secrets
import logging

import httpx
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramAPIError
from app.profile import (ACTIVE_PROFILE_PROMPT_MESSAGE_ID, PERSISTED_PROFILE_SNAPSHOT,
    PROFILE_SECTION_MESSAGE_ID, ProfileSetupStates, profile_payload,
    format_profile_summary, saved_profile_keyboard,
    remove_active_profile_inline_keyboard, _truncate_utf16, is_message_not_modified)

logger = logging.getLogger(__name__)


async def render(message, state, text, keyboard):
    data = await state.get_data()
    target = data.get(ACTIVE_PROFILE_PROMPT_MESSAGE_ID) or data.get(PROFILE_SECTION_MESSAGE_ID)
    if isinstance(target, int):
        try:
            await message.bot.edit_message_text(text, chat_id=message.chat.id,
                message_id=target, reply_markup=keyboard, parse_mode=None)
            return target
        except TelegramAPIError as error:
            if is_message_not_modified(error):
                return target
            logger.warning("Could not edit work history screen", exc_info=True)
        await state.update_data({ACTIVE_PROFILE_PROMPT_MESSAGE_ID: target})
        await remove_active_profile_inline_keyboard(message, state)
    sent = await message.answer(text, reply_markup=keyboard, parse_mode=None)
    return sent.message_id


async def return_to_profile(message, state):
    snapshot = profile_payload((await state.get_data())[PERSISTED_PROFILE_SNAPSHOT])
    canonical = await render(message, state, format_profile_summary(snapshot), saved_profile_keyboard())
    await state.clear()
    await state.set_data({PROFILE_SECTION_MESSAGE_ID: canonical, PERSISTED_PROFILE_SNAPSHOT: snapshot})

FIELDS = ("company", "position", "engagement_kind", "start_year", "start_month", "end_year", "end_month", "is_current")
KINDS = {"unknown": "Не указан", "employment": "Работа по найму", "internship": "Стажировка", "freelance": "Фриланс"}


def payload(entry):
    return {key: entry.get(key, "unknown" if key == "engagement_kind" else None) for key in FIELDS}


def period_part(year, month):
    return "неизвестно" if year is None else (f"{month:02d}/{year}" if month else str(year))


def full(entry):
    end = "сейчас" if entry.get("is_current") is True else period_part(entry.get("end_year"), entry.get("end_month"))
    if entry.get("end_year") is None and entry.get("is_current") is False:
        end = "завершено, дата неизвестна"
    return (f"{entry.get('position') or 'Должность не указана'}\n{entry.get('company') or 'Компания не указана'}\n"
        f"{period_part(entry.get('start_year'), entry.get('start_month'))} → {end}\n"
        f"Формат: {KINDS.get(entry.get('engagement_kind'), 'Не указан')}")


async def screen(message, state, text, buttons, **updates):
    token = secrets.token_hex(4)
    rows = [[InlineKeyboardButton(text=label, callback_data=f"history:{token}:{action}")] for label, action in buttons]
    # Drafts have not passed API validation yet and may exceed Telegram's limit.
    # Only the presentation is bounded; the original draft is submitted unchanged.
    canonical = await render(message, state, _truncate_utf16(text, 4000), InlineKeyboardMarkup(inline_keyboard=rows))
    await state.set_state(ProfileSetupStates.work_history)
    await state.update_data({"history_token": token, ACTIVE_PROFILE_PROMPT_MESSAGE_ID: canonical,
        PROFILE_SECTION_MESSAGE_ID: canonical,
        "history_input": None, "history_actions": [action for _, action in buttons], **updates})


async def additions(message, state, saved=None):
    if saved is not None:
        await state.update_data({PERSISTED_PROFILE_SNAPSHOT: saved, "cv_suggested_experience_saved_profile": saved})
    data = await state.get_data()
    work = data.get("cv_suggested_work_experience", [])
    facts = data.get("cv_suggested_experience_facts", [])
    await screen(message, state, "Профиль сохранён. Проверь дополнительные сведения из резюме.", [
        (f"💼 Места работы · {len(work)}", "category:work"),
        (f"🧾 Практический опыт · {len(facts)}", "category:fact"),
        ("Готово / Пропустить всё", "finish")], history_additions=True)


async def listing(message, state, api, actor, category="work"):
    data = await state.get_data()
    is_additions = data.get("history_additions", False)
    try:
        if is_additions:
            entries = data.get("cv_suggested_work_experience" if category == "work" else "cv_suggested_experience_facts", [])
        else:
            entries = await api.list_work_experiences(await api.create_or_get_user(actor))
    except httpx.HTTPError:
        await message.answer("Не удалось загрузить места работы. Попробуй ещё раз.")
        return
    lines = ["💼 Места работы" if category == "work" else "🧾 Практический опыт"]
    buttons = []
    for index, entry in enumerate(entries):
        label = full(entry) if category == "work" else entry
        lines.append(f"{index + 1}. {_truncate_utf16(label, 150)}")
        buttons.append((f"Проверить {index + 1}", f"select:{index}"))
    if not entries:
        lines.append("Пока нет мест работы." if category == "work" else "Пока нет фактов практического опыта.")
    if not is_additions:
        buttons.append(("➕ Добавить место работы", "add"))
    buttons.append(("↩️ Назад" if is_additions else "↩️ К профилю", "home" if is_additions else "finish"))
    await screen(message, state, "\n\n".join(lines), buttons, history_entries=entries, history_category=category)


async def detail(message, state, api, actor):
    data = await state.get_data()
    entry = data["history_draft"]
    buttons = []
    text = full(entry) if data["history_category"] == "work" else entry
    if data.get("history_additions") and data["history_category"] == "work":
        try:
            existing = await api.list_work_experiences(await api.create_or_get_user(actor))
        except httpx.HTTPError:
            await message.answer("Не удалось проверить сохранённые места работы. Вернись к списку и повтори.")
            return
        # Candidate comparison is exact text identity, only a UI hint; API owns duplicate validation.
        candidates = [item for item in existing if item.get("company") == entry.get("company") and item.get("position") == entry.get("position")]
        if candidates:
            text += "\n\nУже есть запись с такой компанией и должностью. Сравни периоды перед добавлением отдельной записи."
            for candidate in candidates[:2]:
                text += "\n\n" + full(candidate)
    buttons.append(("✅ Сохранить эту запись", "save"))
    if data["history_category"] == "work":
        buttons.extend([(label, "field:" + field) for label, field in (("Компания", "company"), ("Должность", "position"), ("Начало", "start"), ("Окончание", "end"), ("Формат работы", "kind"))])
    else:
        buttons.append(("✏️ Изменить", "field:fact"))
    if data.get("history_additions"):
        buttons.append(("Пропустить запись", "skip"))
    elif data.get("history_id") is not None:
        buttons.append(("🗑 Удалить", "delete"))
    buttons.append(("↩️ К списку", "list"))
    await screen(message, state, text, buttons)


async def entry_callback(callback, state, api):
    await callback.answer()
    message, data = callback.message, await state.get_data()
    if message is None:
        return
    if callback.data == "profile_section:work_history":
        if message.message_id != data.get(PROFILE_SECTION_MESSAGE_ID) or await state.get_state() is not None:
            return
        await state.update_data(history_additions=False)
        await listing(message, state, api, callback.from_user)
        return
    parts = (callback.data or "").split(":")
    if len(parts) < 3 or parts[1] != data.get("history_token") or message.message_id != data.get(ACTIVE_PROFILE_PROMPT_MESSAGE_ID):
        return
    action = parts[2]
    if ":".join(parts[2:]) not in data.get("history_actions", []):
        return
    if action == "finish":
        await return_to_profile(message, state)
    elif action == "home":
        await additions(message, state)
    elif action == "category" and len(parts) == 4 and parts[3] in {"work", "fact"}:
        await listing(message, state, api, callback.from_user, parts[3])
    elif action == "list":
        await listing(message, state, api, callback.from_user, data.get("history_category", "work"))
    elif action == "add":
        await state.update_data(history_draft=payload({}), history_id=None, history_category="work", history_wizard=True)
        await prompt(message, state, "company")
    elif action == "select" and len(parts) == 4 and parts[3].isdigit():
        index = int(parts[3])
        entries = data.get("history_entries", [])
        if index >= len(entries):
            return
        entry = entries[index]
        await state.update_data(history_index=index, history_draft=payload(entry) if data["history_category"] == "work" else entry,
            history_id=entry.get("id") if isinstance(entry, dict) else None, history_wizard=False)
        await detail(message, state, api, callback.from_user)
    elif action == "field" and len(parts) == 4:
        await prompt(message, state, parts[3])
    elif action == "kind" and len(parts) == 4 and parts[3] in KINDS:
        await state.update_data(history_draft={**data["history_draft"], "engagement_kind": parts[3]})
        await detail(message, state, api, callback.from_user)
    elif action == "skip" and data.get("history_additions"):
        await remove_suggestion(message, state, api, callback.from_user)
    elif action == "delete":
        await screen(message, state, "Удалить место работы?\n\n" + full(data["history_draft"]), [("🗑 Подтвердить удаление", "delete_confirm"), ("Отмена", "list")])
    elif action in {"save", "delete_confirm"}:
        try:
            user_id = await api.create_or_get_user(callback.from_user)
            if action == "delete_confirm":
                await api.delete_work_experience(user_id, data["history_id"])
            elif data["history_category"] == "fact":
                await api.create_profile_experience_fact(user_id, data["history_draft"])
            else:
                await api.save_work_experience(user_id, data["history_draft"], data.get("history_id"))
        except httpx.HTTPError as error:
            code = error_code(error)
            if data.get("history_additions") and code in {"DUPLICATE_WORK_EXPERIENCE", "DUPLICATE_EXPERIENCE_FACT"}:
                await message.answer("Эта запись уже добавлена.")
                await remove_suggestion(message, state, api, callback.from_user)
                return
            text = "Достигнут лимит 20 записей. Удали ненужную запись или пропусти предложение." if code in {"WORK_EXPERIENCE_LIMIT_REACHED", "EXPERIENCE_FACT_LIMIT_REACHED"} else "Не удалось сохранить. Проверь поля и период; профиль остаётся сохранённым. Можно исправить запись или повторить."
            await message.answer(text)
            return
        if data.get("history_additions"):
            await remove_suggestion(message, state, api, callback.from_user)
        else:
            await listing(message, state, api, callback.from_user)


def error_code(error):
    if isinstance(error, httpx.HTTPStatusError):
        try:
            detail = error.response.json().get("detail")
            return detail.get("code") if isinstance(detail, dict) else None
        except (ValueError, AttributeError):
            pass
    return None


async def remove_suggestion(message, state, api, actor):
    data = await state.get_data()
    key = "cv_suggested_work_experience" if data["history_category"] == "work" else "cv_suggested_experience_facts"
    items = list(data[key])
    items.pop(data["history_index"])
    await state.update_data({key: items})
    await listing(message, state, api, actor, data["history_category"])


async def prompt(message, state, field):
    if field == "kind":
        await screen(message, state, "Формат работы", [(label, "kind:" + key) for key, label in KINDS.items()] + [("Отмена", "list")])
        return
    prompts = {"company": "Название компании. Если неизвестно, отправь —.", "position": "Должность. Если неизвестно, отправь —.",
        "start": "Начало: ГГГГ или ММ/ГГГГ. Если неизвестно, отправь —.",
        "end": "Окончание: ГГГГ, ММ/ГГГГ, сейчас, завершено (дата неизвестна) или — (статус неизвестен).",
        "fact": "Отправь точный текст факта практического опыта."}
    if field in prompts:
        await screen(message, state, prompts[field], [("Отмена", "list")], history_input=field)


async def receive_text(message, state, api):
    data = await state.get_data()
    field = data.get("history_input")
    if not field:
        return
    text = (message.text or "").strip()
    entry = data["history_draft"]
    if field == "fact":
        entry = text
    else:
        entry = dict(entry)
        if field in {"company", "position"}:
            entry[field] = None if text == "—" else text
        else:
            year = month = None
            if text.lower() not in {"—", "сейчас", "завершено"}:
                match = re.fullmatch(r"(?:(\d{1,2})/)?(\d{4})", text)
                if match is None:
                    await message.answer("Используй ГГГГ или ММ/ГГГГ; неизвестное значение обозначь —.")
                    return
                month, year = int(match[1]) if match[1] else None, int(match[2])
            elif field == "start" and text != "—":
                await message.answer("Для начала укажи год, месяц/год или —.")
                return
            entry.update({field + "_year": year, field + "_month": month})
            if field == "end":
                entry["is_current"] = True if text.lower() == "сейчас" else False if year or text.lower() == "завершено" else None
    await state.update_data(history_draft=entry)
    if data.get("history_wizard") and field in {"company", "position", "start"}:
        await prompt(message, state, {"company": "position", "position": "start", "start": "end"}[field])
    else:
        await state.update_data(history_wizard=False)
        await detail(message, state, api, message.from_user)
