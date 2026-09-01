import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, cast

import httpx
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from app.api_client import BotApiClient
from app.telegram_cleanup import is_message_not_modified

logger = logging.getLogger(__name__)

ROLE_PROMPT = "Какую роль ты ищешь? Например: Python Backend Developer, ML Engineer\nДля отмены — /cancel"
SKILLS_PROMPT = "Какие у тебя основные навыки? Например: Python, FastAPI, PostgreSQL"
EXPERIENCE_PROMPT = "Какой у тебя уровень опыта?"
LOCATION_PROMPT = "Где ты ищешь работу? Укажи города или страны, например: Yerevan, Tbilisi"
WORKPLACE_PROMPT = "Какой формат работы предпочитаешь?"
SALARY_PROMPT = "Какая минимальная зарплата тебе интересна? Например: 2500 USD / month"
LANGUAGES_PROMPT = "Какие языки ты знаешь? Например: English B2, Russian native"
INVALID_LIST_MESSAGE = "Укажи хотя бы одно значение через запятую или используй /cancel."
INVALID_SALARY_MESSAGE = "Используй формат: 2500 USD / month, 60000 EUR / year или нажми «Пропустить»."
INVALID_LANGUAGES_MESSAGE = "Используй формат: English B2, Russian native — или нажми «Пропустить»."
CV_EDIT_INVALID_SALARY_MESSAGE = "Используй формат: 2500 USD / month, 60000 EUR / year или нажми «🗑 Очистить»."
CV_EDIT_INVALID_LANGUAGES_MESSAGE = "Используй формат: English B2, Russian native — или нажми «🗑 Очистить»."
PROFILE_SAVED_MESSAGE = "✅ Профиль сохранён"
PROFILE_CANCELLED_MESSAGE = "Настройка профиля отменена."
CV_DRAFT_CANCELLED_MESSAGE = "Черновик профиля отменён."
PROFILE_API_ERROR_MESSAGE = "Не удалось сохранить профиль. Попробуй ещё раз или используй /cancel."
PROFILE_INVALID_CURRENCY_MESSAGE = (
    "Не удалось сохранить профиль: валюта должна быть действующим ISO-4217 кодом, "
    "например USD или EUR. Попробуй ещё раз или используй /cancel."
)

EXPERIENCE_LABELS = {
    "intern": "Intern",
    "junior": "Junior",
    "middle": "Middle",
    "senior": "Senior",
    "lead": "Lead",
    "unknown": "Не указано",
}
WORKPLACE_LABELS = {
    "remote": "Удалённо",
    "hybrid": "Гибрид",
    "onsite": "На месте работодателя",
    "any": "Любой",
}
PERIOD_LABELS = {"month": "месяц", "year": "год"}
_OPTIONAL_PROMPTS = {
    "skills": SKILLS_PROMPT,
    "location": LOCATION_PROMPT,
    "salary": SALARY_PROMPT,
    "languages": LANGUAGES_PROMPT,
}
ACTIVE_PROFILE_PROMPT_MESSAGE_ID = "active_profile_prompt_message_id"
PROFILE_DRAFT_SOURCE = "profile_draft_source"
PROFILE_EDITABLE_DRAFT_SOURCES = {"manual", "cv", "persisted"}
PROFILE_EDITING_FIELD = "profile_editing_field"
PERSISTED_PROFILE_SNAPSHOT = "persisted_profile_snapshot"
PROFILE_SECTION_MESSAGE_ID = "profile_section_message_id"
PROFILE_SECTION_EDIT_CALLBACK = "profile_section:edit"
PROFILE_EDITABLE_FIELDS = {
    "target_roles",
    "skills",
    "experience",
    "location",
    "workplace_preference",
    "salary",
    "languages",
}
PROFILE_CLEARABLE_FIELDS = {"skills", "location", "salary", "languages"}


class ProfileSetupStates(StatesGroup):
    cv_waiting_document = State()
    cv_processing = State()
    target_roles = State()
    skills = State()
    experience = State()
    location = State()
    workplace_preference = State()
    salary = State()
    languages = State()
    summary = State()
    edit_field = State()


def is_profile_state(state_name: str | None) -> bool:
    return state_name is not None and state_name.startswith(f"{ProfileSetupStates.__name__}:")


async def handle_profile_setup(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ProfileSetupStates.target_roles)
    await state.update_data(**{PROFILE_DRAFT_SOURCE: "manual"})
    await message.answer(ROLE_PROMPT)


async def handle_target_roles(message: Message, state: FSMContext) -> None:
    values = parse_list(message.text or "")
    if not values:
        await message.answer(INVALID_LIST_MESSAGE)
        return
    await state.update_data(target_roles=values)
    await state.set_state(ProfileSetupStates.skills)
    await _ask_optional_text_step(message, state, SKILLS_PROMPT, "skills")


async def handle_skills(message: Message, state: FSMContext) -> None:
    values = parse_list(message.text or "")
    if not values:
        await message.answer(INVALID_LIST_MESSAGE)
        return
    await remove_active_profile_inline_keyboard(message, state)
    await state.update_data(skills=values)
    await ask_experience(message, state)


async def ask_experience(message: Message, state: FSMContext) -> None:
    await state.set_state(ProfileSetupStates.experience)
    prompt_message = await message.answer(
        EXPERIENCE_PROMPT, reply_markup=enum_keyboard("experience", EXPERIENCE_LABELS)
    )
    await state.update_data(active_profile_prompt_message_id=prompt_message.message_id)


async def handle_location(message: Message, state: FSMContext) -> None:
    values = parse_list(message.text or "")
    if not values:
        await message.answer(INVALID_LIST_MESSAGE)
        return
    if any(_is_workplace_location_value(value) for value in values):
        await message.answer("Укажи географические локации, например Yerevan или Armenia. Формат работы выберем отдельно.")
        return
    await remove_active_profile_inline_keyboard(message, state)
    await state.update_data(location=values)
    await ask_workplace(message, state)


async def ask_workplace(message: Message, state: FSMContext) -> None:
    await state.set_state(ProfileSetupStates.workplace_preference)
    prompt_message = await message.answer(
        WORKPLACE_PROMPT, reply_markup=enum_keyboard("workplace", WORKPLACE_LABELS)
    )
    await state.update_data(active_profile_prompt_message_id=prompt_message.message_id)


async def handle_salary(message: Message, state: FSMContext) -> None:
    salary = parse_salary(message.text or "")
    if salary is None:
        await message.answer(INVALID_SALARY_MESSAGE)
        return
    await remove_active_profile_inline_keyboard(message, state)
    await state.update_data(**salary)
    await ask_languages(message, state)


async def ask_languages(message: Message, state: FSMContext) -> None:
    await state.set_state(ProfileSetupStates.languages)
    await _ask_optional_text_step(message, state, LANGUAGES_PROMPT, "languages")


async def handle_languages(message: Message, state: FSMContext) -> None:
    languages = parse_languages(message.text or "")
    if languages is None:
        await message.answer(INVALID_LANGUAGES_MESSAGE)
        return
    await remove_active_profile_inline_keyboard(message, state)
    await state.update_data(languages=languages)
    await show_summary(message, state)


async def handle_profile_callback(
    callback: CallbackQuery, state: FSMContext, api_client: BotApiClient
) -> None:
    data = callback.data or ""
    await callback.answer()
    if callback.message is None:
        return
    message = cast(Message, callback.message)

    parts = data.split(":")
    if len(parts) < 2 or parts[0] != "profile":
        return
    action = parts[1]
    current_state = await state.get_state()

    if not await _is_active_profile_callback(message, state):
        return

    if action == "cancel":
        if (
            current_state == ProfileSetupStates.summary.state
            and (await state.get_data()).get(PROFILE_DRAFT_SOURCE) == "cv"
        ):
            await _delete_cv_draft_summary(message)
            await state.clear()
            await message.answer(CV_DRAFT_CANCELLED_MESSAGE)
            return
        if (
            current_state == ProfileSetupStates.summary.state
            and (await state.get_data()).get(PROFILE_DRAFT_SOURCE) == "persisted"
        ):
            state_data = await state.get_data()
            snapshot = state_data.get(PERSISTED_PROFILE_SNAPSHOT)
            if not isinstance(snapshot, dict):
                return
            await show_saved_profile_card(message, state, snapshot, replace_summary=True)
            return
        if is_profile_state(current_state):
            await _remove_inline_keyboard(message)
            await handle_profile_cancel(message, state)
        return
    if action == "save":
        is_persisted_draft = (
            (await state.get_data()).get(PROFILE_DRAFT_SOURCE) == "persisted"
        )
        if await save_profile(message, callback.from_user, state, api_client):
            if not is_persisted_draft:
                await _remove_inline_keyboard(message)
        return
    if action == "edit_draft":
        if current_state != ProfileSetupStates.summary.state:
            return
        data = await state.get_data()
        if data.get(PROFILE_DRAFT_SOURCE) not in PROFILE_EDITABLE_DRAFT_SOURCES:
            return
        await _remove_inline_keyboard(message)
        await _show_profile_edit_field_picker(message, state)
        return
    if action == "edit" and len(parts) == 3:
        field = parts[2]
        if (
            current_state != ProfileSetupStates.edit_field.state
            or field not in PROFILE_EDITABLE_FIELDS
            or (await state.get_data()).get(PROFILE_DRAFT_SOURCE)
            not in PROFILE_EDITABLE_DRAFT_SOURCES
        ):
            return
        await _remove_inline_keyboard(message)
        await _prompt_profile_edit_field(message, state, field)
        return
    if action == "edit_clear" and len(parts) == 3:
        field = parts[2]
        if (
            current_state != ProfileSetupStates.edit_field.state
            or field not in PROFILE_CLEARABLE_FIELDS
            or (await state.get_data()).get(PROFILE_EDITING_FIELD) != field
        ):
            return
        await _remove_inline_keyboard(message)
        if field == "salary":
            await state.update_data(salary_min=None, salary_currency=None, salary_period="unknown")
        else:
            await state.update_data({field: []})
        await _return_profile_edit_to_summary(message, state)
        return
    if action == "edit_enum" and len(parts) == 4:
        field, value = parts[2], parts[3]
        labels = {
            "experience": EXPERIENCE_LABELS,
            "workplace_preference": WORKPLACE_LABELS,
        }.get(field)
        if (
            current_state != ProfileSetupStates.edit_field.state
            or labels is None
            or value not in labels
            or (await state.get_data()).get(PROFILE_EDITING_FIELD) != field
        ):
            return
        await _mark_prompt_choice(
            message, _profile_edit_enum_prompt(field, await state.get_data()), labels[value]
        )
        await state.update_data({field: value})
        await _return_profile_edit_to_summary(message, state)
        return
    if action == "skip" and len(parts) == 3:
        if await handle_skip(message, state, parts[2]):
            await _mark_prompt_skipped(message, _OPTIONAL_PROMPTS[parts[2]])
        return
    if (
        action == "experience"
        and current_state == ProfileSetupStates.experience.state
        and len(parts) == 3
        and parts[2] in EXPERIENCE_LABELS
    ):
        await _mark_prompt_choice(message, EXPERIENCE_PROMPT, EXPERIENCE_LABELS[parts[2]])
        await state.update_data(experience=parts[2])
        await state.set_state(ProfileSetupStates.location)
        await _ask_optional_text_step(message, state, LOCATION_PROMPT, "location")
        return
    if (
        action == "workplace"
        and current_state == ProfileSetupStates.workplace_preference.state
        and len(parts) == 3
        and parts[2] in WORKPLACE_LABELS
    ):
        await _mark_prompt_choice(message, WORKPLACE_PROMPT, WORKPLACE_LABELS[parts[2]])
        await state.update_data(workplace_preference=parts[2])
        await state.set_state(ProfileSetupStates.salary)
        await _ask_optional_text_step(message, state, SALARY_PROMPT, "salary")


async def handle_skip(message: Message, state: FSMContext, field: str) -> bool:
    current_state = await state.get_state()
    expected = {
        "skills": ProfileSetupStates.skills.state,
        "location": ProfileSetupStates.location.state,
        "salary": ProfileSetupStates.salary.state,
        "languages": ProfileSetupStates.languages.state,
    }
    expected_state = expected.get(field)
    if expected_state is None or current_state != expected_state:
        return False
    if field == "skills":
        await state.update_data(skills=[])
        await ask_experience(message, state)
    elif field == "location":
        await state.update_data(location=[])
        await ask_workplace(message, state)
    elif field == "salary":
        await state.update_data(salary_min=None, salary_currency=None, salary_period="unknown")
        await ask_languages(message, state)
    elif field == "languages":
        await state.update_data(languages=[])
        await show_summary(message, state)
    return True


async def show_summary(message: Message, state: FSMContext) -> None:
    await state.set_state(ProfileSetupStates.summary)
    state_data = await state.get_data()
    data = profile_payload(state_data)
    include_edit = state_data.get(PROFILE_DRAFT_SOURCE) in PROFILE_EDITABLE_DRAFT_SOURCES
    prompt_message = await message.answer(
        format_profile_summary(data), reply_markup=summary_keyboard(include_edit=include_edit)
    )
    await state.update_data(active_profile_prompt_message_id=prompt_message.message_id)


async def save_profile(
    message: Message, telegram_user: User, state: FSMContext, api_client: BotApiClient
) -> bool:
    if await state.get_state() != ProfileSetupStates.summary.state:
        return False
    state_data = await state.get_data()
    source = state_data.get(PROFILE_DRAFT_SOURCE)
    payload = profile_payload(state_data)
    try:
        user_id = await api_client.create_or_get_user(telegram_user)
        saved_profile = await api_client.put_user_profile(user_id, payload)
    except httpx.HTTPError as error:
        logger.exception("Could not save user profile through API")
        error_message = (
            PROFILE_INVALID_CURRENCY_MESSAGE
            if isinstance(error, httpx.HTTPStatusError) and _is_invalid_salary_currency_error(error)
            else PROFILE_API_ERROR_MESSAGE
        )
        include_edit = source in PROFILE_EDITABLE_DRAFT_SOURCES
        retry_message = await message.answer(
            error_message, reply_markup=summary_keyboard(include_edit=include_edit)
        )
        await _remove_inline_keyboard(message)
        await state.update_data(active_profile_prompt_message_id=retry_message.message_id)
        return False
    if source == "persisted":
        await show_saved_profile_card(message, state, saved_profile, replace_summary=True)
    else:
        await state.clear()
        await message.answer(PROFILE_SAVED_MESSAGE)
    return True


async def handle_profile_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(PROFILE_CANCELLED_MESSAGE)


async def handle_profile_draft_field_input(message: Message, state: FSMContext) -> None:
    if await state.get_state() != ProfileSetupStates.edit_field.state:
        return
    data = await state.get_data()
    field = data.get(PROFILE_EDITING_FIELD)
    if not isinstance(field, str):
        return

    if field in {"target_roles", "skills", "location"}:
        values = parse_list(message.text or "")
        if not values:
            await message.answer(INVALID_LIST_MESSAGE)
            return
        if field == "location" and any(_is_workplace_location_value(value) for value in values):
            await message.answer(
                "Укажи географические локации, например Yerevan или Armenia. Формат работы выбирается отдельно."
            )
            return
        await state.update_data({field: values})
    elif field == "salary":
        salary = parse_salary(message.text or "")
        if salary is None:
            await message.answer(CV_EDIT_INVALID_SALARY_MESSAGE)
            return
        await state.update_data(**salary)
    elif field == "languages":
        languages = parse_languages(message.text or "")
        if languages is None:
            await message.answer(CV_EDIT_INVALID_LANGUAGES_MESSAGE)
            return
        await state.update_data(languages=languages)
    else:
        return

    await remove_active_profile_inline_keyboard(message, state)
    await _return_profile_edit_to_summary(message, state)


async def show_persisted_profile_editor(message: Message, state: FSMContext) -> None:
    state_data = await state.get_data()
    snapshot = state_data.get(PERSISTED_PROFILE_SNAPSHOT)
    if not isinstance(snapshot, dict):
        return
    snapshot_payload = profile_payload(snapshot)
    editor_data = {
        **snapshot_payload,
        PROFILE_DRAFT_SOURCE: "persisted",
        PERSISTED_PROFILE_SNAPSHOT: snapshot_payload,
    }
    section_message_id = state_data.get(PROFILE_SECTION_MESSAGE_ID)
    if isinstance(section_message_id, int):
        editor_data[PROFILE_SECTION_MESSAGE_ID] = section_message_id
    await state.set_data(editor_data)
    await _show_profile_edit_field_picker(message, state)


async def _show_profile_edit_field_picker(message: Message, state: FSMContext) -> None:
    await state.set_state(ProfileSetupStates.edit_field)
    prompt_message = await message.answer(
        "Какое поле изменить?", reply_markup=profile_edit_field_keyboard()
    )
    await state.update_data(active_profile_prompt_message_id=prompt_message.message_id)


async def _prompt_profile_edit_field(message: Message, state: FSMContext, field: str) -> None:
    data = await state.get_data()
    prompts: dict[str, tuple[str, InlineKeyboardMarkup | None]] = {
        "target_roles": (
            f"Текущие роли:\n{_render_list(data.get('target_roles'))}\n\nОтправь обновлённый список.",
            None,
        ),
        "skills": (
            f"Текущие навыки:\n{_render_list(data.get('skills'))}\n\nОтправь обновлённый список.",
            profile_edit_clear_keyboard("skills"),
        ),
        "location": (
            f"Текущие локации:\n{_render_list(data.get('location'))}\n\nОтправь обновлённый список.",
            profile_edit_clear_keyboard("location"),
        ),
        "salary": (
            f"Текущая зарплата:\n{_render_salary(data)}\n\nОтправь новое значение в формате: 2500 USD / month.",
            profile_edit_clear_keyboard("salary"),
        ),
        "languages": (
            f"Текущие языки:\n{_render_languages(data.get('languages'))}\n\nОтправь обновлённый список.",
            profile_edit_clear_keyboard("languages"),
        ),
    }
    if field in {"experience", "workplace_preference"}:
        prompt = _profile_edit_enum_prompt(field, data)
        labels = EXPERIENCE_LABELS if field == "experience" else WORKPLACE_LABELS
        keyboard = profile_edit_enum_keyboard(field, labels)
    else:
        prompt, keyboard = prompts[field]
    await state.update_data({PROFILE_EDITING_FIELD: field})
    prompt_message = await message.answer(prompt, reply_markup=keyboard)
    await state.update_data(active_profile_prompt_message_id=prompt_message.message_id)


def _profile_edit_enum_prompt(field: str, data: dict[str, object]) -> str:
    if field == "experience":
        current = EXPERIENCE_LABELS.get(str(data.get("experience")), "Не указано")
        return f"Текущий опыт: {current}\n\nВыбери новое значение."
    current = WORKPLACE_LABELS.get(str(data.get("workplace_preference")), "Любой")
    return f"Текущий формат работы: {current}\n\nВыбери новое значение."


async def _return_profile_edit_to_summary(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    data.pop(PROFILE_EDITING_FIELD, None)
    await state.set_data(data)
    await show_summary(message, state)


async def _remove_inline_keyboard(message: Message) -> None:
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as error:
        if is_message_not_modified(error):
            return
        logger.warning("Could not remove profile inline keyboard", exc_info=True)
    except TelegramAPIError:
        logger.warning("Could not remove profile inline keyboard", exc_info=True)


async def _delete_cv_draft_summary(message: Message) -> None:
    try:
        await message.delete()
    except TelegramAPIError:
        logger.warning("Could not delete CV draft summary", exc_info=True)
        await _remove_inline_keyboard(message)


async def _mark_prompt_choice(message: Message, prompt: str, label: str) -> None:
    try:
        await message.edit_text(f"{prompt}\n✅ {label}", reply_markup=None)
    except TelegramAPIError:
        logger.warning("Could not mark profile inline choice", exc_info=True)


async def _mark_prompt_skipped(message: Message, prompt: str) -> None:
    try:
        await message.edit_text(f"{prompt}\n⏭ Пропущено", reply_markup=None)
    except TelegramAPIError:
        logger.warning("Could not mark skipped profile prompt", exc_info=True)


async def _ask_optional_text_step(message: Message, state: FSMContext, prompt: str, field: str) -> None:
    prompt_message = await message.answer(prompt, reply_markup=skip_keyboard(field))
    await state.update_data(
        skip_prompt_message_id=prompt_message.message_id,
        active_profile_prompt_message_id=prompt_message.message_id,
    )


async def remove_active_profile_inline_keyboard(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    prompt_message_id = data.get(ACTIVE_PROFILE_PROMPT_MESSAGE_ID, data.get("skip_prompt_message_id"))
    if not isinstance(prompt_message_id, int) or message.bot is None:
        return
    try:
        await message.bot.edit_message_reply_markup(
            chat_id=message.chat.id, message_id=prompt_message_id, reply_markup=None
        )
    except TelegramBadRequest as error:
        if is_message_not_modified(error):
            return
        logger.warning("Could not remove active profile inline keyboard", exc_info=True)
    except TelegramAPIError:
        logger.warning("Could not remove active profile inline keyboard", exc_info=True)


async def _is_active_profile_callback(message: Message, state: FSMContext) -> bool:
    active_message_id = (await state.get_data()).get(ACTIVE_PROFILE_PROMPT_MESSAGE_ID)
    if not isinstance(active_message_id, int):
        return False
    message_id = getattr(message, "message_id", None)
    return isinstance(message_id, int) and message_id == active_message_id


def _is_invalid_salary_currency_error(error: httpx.HTTPStatusError) -> bool:
    if error.response.status_code != 422:
        return False
    try:
        detail = error.response.json().get("detail")
    except ValueError:
        return False
    if not isinstance(detail, list):
        return False
    for item in detail:
        if not isinstance(item, dict):
            continue
        location = item.get("loc")
        message = item.get("msg")
        if (
            isinstance(location, list)
            and location[-1:] == ["salary_currency"]
            and isinstance(message, str)
            and "active ISO 4217" in message
        ):
            return True
    return False


def profile_payload(data: dict[str, object]) -> dict[str, object]:
    return {
        "target_roles": data.get("target_roles", []),
        "skills": data.get("skills", []),
        "experience": data.get("experience", "unknown"),
        "location": data.get("location", []),
        "workplace_preference": data.get("workplace_preference", "any"),
        "salary_min": data.get("salary_min"),
        "salary_currency": data.get("salary_currency"),
        "salary_period": data.get("salary_period", "unknown"),
        "languages": data.get("languages", []),
    }


def _is_workplace_location_value(value: str) -> bool:
    normalized = _normalize_workplace_location_value(value)
    return normalized in {
        *(_normalize_workplace_location_value(item) for item in WORKPLACE_LABELS),
        *(_normalize_workplace_location_value(label) for label in WORKPLACE_LABELS.values()),
    }


def _normalize_workplace_location_value(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def parse_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_salary(value: str) -> dict[str, Any] | None:
    match = re.fullmatch(
        r"\s*([0-9]+(?:[.,][0-9]{1,2})?)\s+([A-Za-z]{3})\s*/\s*(month|year)\s*",
        value,
        re.I,
    )
    if match is None:
        return None
    try:
        amount = Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:
        return None
    if amount <= 0:
        return None
    return {
        "salary_min": str(amount),
        "salary_currency": match.group(2).upper(),
        "salary_period": match.group(3).lower(),
    }


def parse_languages(value: str) -> list[dict[str, str]] | None:
    parsed: list[dict[str, str]] = []
    for item in parse_list(value):
        parts = item.rsplit(maxsplit=1)
        if len(parts) != 2:
            return None
        parsed.append({"language": parts[0], "level": parts[1]})
    return parsed or None


def format_profile_summary(data: dict[str, object]) -> str:
    rendered_languages = _render_languages(data.get("languages"))
    salary = _render_salary(data)
    return "\n".join(
        (
            f"🎯 Целевые роли: {_render_list(data.get('target_roles'))}",
            f"🧩 Навыки: {_render_list(data.get('skills'))}",
            f"📈 Опыт: {EXPERIENCE_LABELS.get(str(data.get('experience')), 'Не указано')}",
            f"📍 Локация: {_render_list(data.get('location'))}",
            f"🏠 Формат работы: {WORKPLACE_LABELS.get(str(data.get('workplace_preference')), 'Не указано')}",
            f"💰 Зарплата: {salary}",
            f"🌍 Языки: {rendered_languages or 'Не указаны'}",
        )
    )


def _render_list(value: object) -> str:
    if not isinstance(value, list):
        return "Не указаны"
    rendered = ", ".join(str(item) for item in value)
    return rendered or "Не указаны"


def _render_languages(value: object) -> str:
    languages = value if isinstance(value, list) else []
    rendered = ", ".join(
        f"{item['language']} {item['level']}"
        for item in languages
        if isinstance(item, dict)
        and isinstance(item.get("language"), str)
        and isinstance(item.get("level"), str)
    )
    return rendered or "Не указаны"


def _render_salary(data: dict[str, object]) -> str:
    if data.get("salary_min") is None:
        return "Не указана"
    period = PERIOD_LABELS.get(str(data.get("salary_period")), str(data.get("salary_period")))
    return f"{data['salary_min']} {data['salary_currency']} / {period}"


def enum_keyboard(field: str, labels: dict[str, str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"profile:{field}:{value}")]
            for value, label in labels.items()
        ]
    )


def skip_keyboard(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data=f"profile:skip:{field}")]]
    )


def profile_edit_field_keyboard() -> InlineKeyboardMarkup:
    labels = (
        ("target_roles", "🎯 Роли"),
        ("skills", "🧩 Навыки"),
        ("experience", "📈 Опыт"),
        ("location", "📍 Локации"),
        ("workplace_preference", "🏠 Формат работы"),
        ("salary", "💰 Зарплата"),
        ("languages", "🌍 Языки"),
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"profile:edit:{field}")]
            for field, label in labels
        ]
    )


def profile_edit_clear_keyboard(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Очистить", callback_data=f"profile:edit_clear:{field}")]
        ]
    )


def profile_edit_enum_keyboard(field: str, labels: dict[str, str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label, callback_data=f"profile:edit_enum:{field}:{value}"
                )
            ]
            for value, label in labels.items()
        ]
    )


def summary_keyboard(*, include_edit: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="✅ Сохранить", callback_data="profile:save")]]
    if include_edit:
        rows.append(
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="profile:edit_draft")]
        )
    rows.append([InlineKeyboardButton(text="❌ Отменить", callback_data="profile:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def saved_profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить", callback_data=PROFILE_SECTION_EDIT_CALLBACK)]
        ]
    )


async def show_saved_profile_card(
    message: Message, state: FSMContext, profile: dict[str, object], *, replace_summary: bool = False
) -> None:
    snapshot = profile_payload(profile)
    await _remove_previous_saved_profile_card(message, state)
    if replace_summary:
        await _delete_profile_summary_message(message)
    await state.clear()
    card = await message.answer(
        format_profile_summary(snapshot), reply_markup=saved_profile_keyboard()
    )
    await state.set_data(
        {
            PROFILE_SECTION_MESSAGE_ID: card.message_id,
            PERSISTED_PROFILE_SNAPSHOT: snapshot,
        }
    )


async def _remove_previous_saved_profile_card(message: Message, state: FSMContext) -> None:
    section_message_id = (await state.get_data()).get(PROFILE_SECTION_MESSAGE_ID)
    if not isinstance(section_message_id, int) or message.bot is None:
        return
    try:
        await message.bot.delete_message(chat_id=message.chat.id, message_id=section_message_id)
        return
    except TelegramAPIError:
        logger.warning("Could not delete previous saved profile card", exc_info=True)
    try:
        await message.bot.edit_message_reply_markup(
            chat_id=message.chat.id, message_id=section_message_id, reply_markup=None
        )
    except TelegramBadRequest as error:
        if is_message_not_modified(error):
            return
        logger.warning("Could not remove previous saved profile card keyboard", exc_info=True)
    except TelegramAPIError:
        logger.warning("Could not remove previous saved profile card keyboard", exc_info=True)


async def _delete_profile_summary_message(message: Message) -> None:
    try:
        await message.delete()
    except TelegramAPIError:
        logger.warning("Could not delete profile summary message", exc_info=True)
