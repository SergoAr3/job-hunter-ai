import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, cast

import httpx
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from app.api_client import BotApiClient

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
PROFILE_SAVED_MESSAGE = "✅ Профиль сохранён"
PROFILE_CANCELLED_MESSAGE = "Настройка профиля отменена."
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
    "unknown": "Не указывать",
}
WORKPLACE_LABELS = {
    "remote": "Remote",
    "hybrid": "Hybrid",
    "onsite": "Onsite",
    "any": "Любой",
}
PERIOD_LABELS = {"month": "месяц", "year": "год"}
_OPTIONAL_PROMPTS = {
    "skills": SKILLS_PROMPT,
    "location": LOCATION_PROMPT,
    "salary": SALARY_PROMPT,
    "languages": LANGUAGES_PROMPT,
}


class ProfileSetupStates(StatesGroup):
    target_roles = State()
    skills = State()
    experience = State()
    location = State()
    workplace_preference = State()
    salary = State()
    languages = State()
    summary = State()


def is_profile_state(state_name: str | None) -> bool:
    return state_name is not None and state_name.startswith(f"{ProfileSetupStates.__name__}:")


async def handle_profile_setup(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ProfileSetupStates.target_roles)
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
    await _remove_active_skip_keyboard(message, state)
    await state.update_data(skills=values)
    await ask_experience(message, state)


async def ask_experience(message: Message, state: FSMContext) -> None:
    await state.set_state(ProfileSetupStates.experience)
    await message.answer(EXPERIENCE_PROMPT, reply_markup=enum_keyboard("experience", EXPERIENCE_LABELS))


async def handle_location(message: Message, state: FSMContext) -> None:
    values = parse_list(message.text or "")
    if not values:
        await message.answer(INVALID_LIST_MESSAGE)
        return
    if any(value.casefold() in WORKPLACE_LABELS for value in values):
        await message.answer("Укажи географические локации, например Yerevan или Armenia. Формат работы выберем отдельно.")
        return
    await _remove_active_skip_keyboard(message, state)
    await state.update_data(location=values)
    await ask_workplace(message, state)


async def ask_workplace(message: Message, state: FSMContext) -> None:
    await state.set_state(ProfileSetupStates.workplace_preference)
    await message.answer(WORKPLACE_PROMPT, reply_markup=enum_keyboard("workplace", WORKPLACE_LABELS))


async def handle_salary(message: Message, state: FSMContext) -> None:
    salary = parse_salary(message.text or "")
    if salary is None:
        await message.answer(INVALID_SALARY_MESSAGE)
        return
    await _remove_active_skip_keyboard(message, state)
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
    await _remove_active_skip_keyboard(message, state)
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

    if action == "cancel":
        if is_profile_state(current_state):
            await _remove_inline_keyboard(message)
            await handle_profile_cancel(message, state)
        return
    if action == "save":
        if await save_profile(message, callback.from_user, state, api_client):
            await _remove_inline_keyboard(message)
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
    data = profile_payload(await state.get_data())
    await message.answer(format_profile_summary(data), reply_markup=summary_keyboard())


async def save_profile(
    message: Message, telegram_user: User, state: FSMContext, api_client: BotApiClient
) -> bool:
    if await state.get_state() != ProfileSetupStates.summary.state:
        return False
    payload = profile_payload(await state.get_data())
    try:
        user_id = await api_client.create_or_get_user(telegram_user)
        await api_client.put_user_profile(user_id, payload)
    except httpx.HTTPError as error:
        logger.exception("Could not save user profile through API")
        error_message = (
            PROFILE_INVALID_CURRENCY_MESSAGE
            if isinstance(error, httpx.HTTPStatusError) and _is_invalid_salary_currency_error(error)
            else PROFILE_API_ERROR_MESSAGE
        )
        await message.answer(error_message, reply_markup=summary_keyboard())
        return False
    await state.clear()
    await message.answer(PROFILE_SAVED_MESSAGE)
    return True


async def handle_profile_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(PROFILE_CANCELLED_MESSAGE)


async def _remove_inline_keyboard(message: Message) -> None:
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramAPIError:
        logger.warning("Could not remove profile inline keyboard", exc_info=True)


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
    await state.update_data(skip_prompt_message_id=prompt_message.message_id)


async def _remove_active_skip_keyboard(message: Message, state: FSMContext) -> None:
    prompt_message_id = (await state.get_data()).get("skip_prompt_message_id")
    if not isinstance(prompt_message_id, int) or message.bot is None:
        return
    try:
        await message.bot.edit_message_reply_markup(
            chat_id=message.chat.id, message_id=prompt_message_id, reply_markup=None
        )
    except TelegramAPIError:
        logger.warning("Could not remove active profile skip keyboard", exc_info=True)


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
    language_data = data.get("languages", [])
    languages = language_data if isinstance(language_data, list) else []
    rendered_languages = ", ".join(
        f"{item['language']} {item['level']}"
        for item in languages
        if isinstance(item, dict)
        and isinstance(item.get("language"), str)
        and isinstance(item.get("level"), str)
    )
    salary = "Не указана"
    if data.get("salary_min") is not None:
        period = PERIOD_LABELS.get(str(data.get("salary_period")), str(data.get("salary_period")))
        salary = f"{data['salary_min']} {data['salary_currency']} / {period}"
    return "\n".join(
        (
            f"🎯 Роли: {_render_list(data.get('target_roles'))}",
            f"🧩 Навыки: {_render_list(data.get('skills'))}",
            f"📈 Опыт: {EXPERIENCE_LABELS.get(str(data.get('experience')), str(data.get('experience')))}",
            f"📍 Локации: {_render_list(data.get('location'))}",
            f"🏠 Формат: {WORKPLACE_LABELS.get(str(data.get('workplace_preference')), str(data.get('workplace_preference')))}",
            f"💰 Минимум: {salary}",
            f"🌍 Языки: {rendered_languages or 'Не указаны'}",
        )
    )


def _render_list(value: object) -> str:
    if not isinstance(value, list):
        return "Не указаны"
    rendered = ", ".join(str(item) for item in value)
    return rendered or "Не указаны"


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


def summary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сохранить", callback_data="profile:save")],
            [InlineKeyboardButton(text="Отменить", callback_data="profile:cancel")],
        ]
    )
