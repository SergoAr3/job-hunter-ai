import logging
from urllib.parse import urlsplit

import httpx
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User
from aiogram.utils.chat_action import ChatActionSender

from app.api_client import BotApiClient
from app.telegram_cleanup import is_message_not_modified

logger = logging.getLogger(__name__)

REQUEST_URL_MESSAGE = "Пришли ссылку на вакансию. Для отмены — /cancel"
INVALID_URL_MESSAGE = "Нужна ссылка формата https://… или /cancel"
UNSAFE_URL_MESSAGE = "Эту ссылку нельзя использовать. Пришли другую ссылку или /cancel"
API_UNAVAILABLE_MESSAGE = "Не удалось сохранить вакансию. Пришли ссылку ещё раз или /cancel"
SAVED_MESSAGE = "Вакансия сохранена ✅"
ALREADY_SAVED_MESSAGE = "Эта вакансия уже сохранена."
CANCELLED_MESSAGE = "Добавление вакансии отменено."
NO_ACTIVE_FLOW_MESSAGE = "Сейчас нет активного действия."
PROCESSING_MESSAGE = "Сохраняю и анализирую вакансию…"
MATCH_DETAILS_PREFIX = "match:details:"
MATCH_PROFILE_CALLBACK = "match:profile"
ACTIVE_MATCH_MESSAGE_ID = "active_match_message_id"
ACTIVE_MATCH_DETAILS_CLAIM_ID = "active_match_details_claim_id"


class AddJobStates(StatesGroup):
    waiting_for_url = State()


async def handle_add_job(message: Message, state: FSMContext) -> None:
    await remove_active_match_inline_keyboard(message, state)
    await state.clear()
    await state.set_state(AddJobStates.waiting_for_url)
    await message.answer(REQUEST_URL_MESSAGE)


async def handle_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer(NO_ACTIVE_FLOW_MESSAGE)
        return

    await state.clear()
    await message.answer(CANCELLED_MESSAGE)


async def handle_job_url(message: Message, state: FSMContext, api_client: BotApiClient) -> None:
    source_url = message.text or ""
    if not _is_basic_http_url(source_url):
        await message.answer(INVALID_URL_MESSAGE)
        return

    telegram_user = message.from_user
    if telegram_user is None:
        return

    processing_message = await message.answer(PROCESSING_MESSAGE)
    try:
        result, user_id = await _save_application_with_typing(message, api_client, telegram_user, source_url)
    except httpx.HTTPStatusError as error:
        await _delete_processing_message(processing_message)
        if error.response.status_code == 422:
            try:
                detail = error.response.json().get("detail")
            except ValueError:
                detail = None
            await message.answer(UNSAFE_URL_MESSAGE if detail == "Unsafe URL" else INVALID_URL_MESSAGE)
            return
        logger.exception("Could not save job through API")
        await message.answer(API_UNAVAILABLE_MESSAGE)
        return
    except httpx.HTTPError:
        await _delete_processing_message(processing_message)
        logger.exception("Could not save job through API")
        await message.answer(API_UNAVAILABLE_MESSAGE)
        return

    await _delete_processing_message(processing_message)
    await state.clear()
    if result["application_created"]:
        await message.answer(SAVED_MESSAGE)
    else:
        await message.answer(ALREADY_SAVED_MESSAGE)
        return
    job = result.get("job")
    if isinstance(job, dict):
        status = job.get("parsing_status")
        if status == "partial":
            await message.answer("Удалось получить не все данные о вакансии.")
        elif status == "failed":
            await message.answer("Но автоматически разобрать вакансию не удалось.")
        if status in {"success", "partial"}:
            card = format_job_card(job)
            if card:
                await message.answer(card)
    application = result.get("application")
    if isinstance(application, dict):
        application_id = application.get("id")
        if isinstance(application_id, int) and not isinstance(application_id, bool):
            await _show_match_summary(message, state, api_client, user_id, application_id)


async def _save_application_with_typing(
    message: Message,
    api_client: BotApiClient,
    telegram_user: User,
    source_url: str,
) -> tuple[dict[str, object], int]:
    sender: ChatActionSender | None = None
    try:
        bot: Bot | None = message.bot
        if bot is None:
            raise RuntimeError("Message is not bound to a bot")
        sender = ChatActionSender.typing(chat_id=message.chat.id, bot=bot)
        await sender.__aenter__()
    except Exception:
        sender = None
        logger.warning("Could not start Telegram typing indicator", exc_info=True)

    try:
        user_id = await api_client.create_or_get_user(telegram_user)
        return await api_client.save_application(user_id, source_url), user_id
    finally:
        if sender is not None:
            try:
                await sender.__aexit__(None, None, None)
            except Exception:
                logger.warning("Could not stop Telegram typing indicator", exc_info=True)


async def _delete_processing_message(processing_message: Message) -> None:
    try:
        await processing_message.delete()
    except Exception:
        logger.warning("Could not delete Telegram processing message", exc_info=True)


def _is_basic_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def format_job_card(job: dict[str, object]) -> str:
    salary = job.get("salary_text") or _format_structured_salary(job)
    workplace = {"any": "Любой", "remote": "Удалённо", "hybrid": "Гибрид", "onsite": "На месте работодателя"}.get(job.get("workplace_type"), job.get("workplace_type"))
    fields = [("Вакансия", job.get("title")), ("Компания", job.get("company")), ("Локация", job.get("location")), ("Формат", workplace), ("Тип занятости", job.get("employment_type")), ("Зарплата", salary)]
    lines = [f"{label}: {value}" for label, value in fields if value and value != "unknown"]
    if job.get("ai_enrichment_status") == "success":
        for label, key, limit in (("Навыки", "required_skills", 8), ("Будет плюсом", "nice_to_have_skills", 6), ("Опыт", "experience_requirements", 4), ("Языки", "language_requirements", 4), ("Задачи", "responsibilities", 4)):
            values = job.get(key)
            if isinstance(values, list):
                display_values: list[str] = []
                for value in values[:limit]:
                    if isinstance(value, str) and value:
                        display_values.append(value)
                rendered = "; ".join(display_values)
                if rendered:
                    lines.append(f"{label}: {rendered}")
        seniority = job.get("seniority")
        if isinstance(seniority, str) and seniority != "unknown":
            lines.append(f"Уровень: {seniority}")
    if not lines: return ""
    for label, key, limit in (("Описание", "description", 2500), ("Требования", "requirements_text", 700)):
        value = job.get(key)
        if isinstance(value, str) and value:
            lines.append(f"{label}: {value[:limit]}{'…' if len(value) > limit else ''}")
    return "\n".join(lines)[:3800]


def _format_structured_salary(job: dict[str, object]) -> str | None:
    minimum, maximum, currency, period = job.get("salary_min"), job.get("salary_max"), job.get("salary_currency"), job.get("salary_period")
    if minimum is None and maximum is None:
        return None
    amount = str(minimum) if maximum is None else str(maximum) if minimum is None else f"{minimum}–{maximum}"
    parts = [amount]
    if isinstance(currency, str) and currency:
        parts.append(currency)
    if isinstance(period, str) and period != "unknown":
        parts.append(f"per {period}")
    return " ".join(parts)


async def _show_match_summary(
    message: Message, state: FSMContext, api_client: BotApiClient, user_id: int, application_id: int
) -> None:
    try:
        match = await api_client.get_application_match(user_id, application_id)
    except httpx.HTTPStatusError as error:
        if _error_code(error) == "PROFILE_REQUIRED":
            await _send_match_message(message, state, "🎯 Заполни профиль, чтобы оценить совпадение.", MATCH_PROFILE_CALLBACK)
        else:
            logger.warning("Could not get match through API", exc_info=True)
        return
    except httpx.HTTPError:
        logger.warning("Could not get match through API", exc_info=True)
        return
    verdict = match.get("verdict")
    score = match.get("score")
    if verdict == "insufficient_data" or not isinstance(score, int) or isinstance(score, bool):
        await message.answer("🎯 Недостаточно данных для надёжной оценки.")
        return
    await _send_match_message(message, state, f"🎯 Совпадение: {score}%", f"{MATCH_DETAILS_PREFIX}{application_id}")


async def _send_match_message(message: Message, state: FSMContext, text: str, callback_data: str) -> None:
    summary = await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔎 Почему подходит?" if callback_data.startswith(MATCH_DETAILS_PREFIX) else "👤 Заполнить профиль", callback_data=callback_data)]]
        ),
    )
    await state.update_data(
        {ACTIVE_MATCH_MESSAGE_ID: summary.message_id, ACTIVE_MATCH_DETAILS_CLAIM_ID: None}
    )


async def handle_match_callback(callback: CallbackQuery, state: FSMContext, api_client: BotApiClient) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    message = callback.message
    data = callback.data or ""
    if data.startswith(MATCH_DETAILS_PREFIX):
        application_id_text = data.removeprefix(MATCH_DETAILS_PREFIX)
        if not application_id_text.isdecimal() or not await _claim_active_match_details(message, state):
            await callback.answer()
            return
        await callback.answer()
        try:
            user_id = await api_client.create_or_get_user(callback.from_user)
            match = await api_client.get_application_match(user_id, int(application_id_text))
        except httpx.HTTPStatusError as error:
            if _error_code(error) == "PROFILE_REQUIRED":
                await _replace_claimed_match_with_profile_cta(message, state)
            else:
                await _restore_match_details_claim(message, state)
                logger.warning("Could not refresh match through API", exc_info=True)
            return
        except httpx.HTTPError:
            await _restore_match_details_claim(message, state)
            logger.warning("Could not refresh match through API", exc_info=True)
            return
        if not await _has_match_details_claim(message, state):
            return
        await _remove_match_keyboard(message)
        if not await _has_match_details_claim(message, state):
            return
        await state.update_data({ACTIVE_MATCH_DETAILS_CLAIM_ID: None})
        await message.answer(format_match_details(match))
        return

    await callback.answer()
    if not await _is_active_match_callback(message, state):
        return
    if data == MATCH_PROFILE_CALLBACK:
        await _remove_match_keyboard(message)
        from app.profile import handle_profile_setup

        await handle_profile_setup(message, state)
        return


async def _claim_active_match_details(message: Message, state: FSMContext) -> bool:
    if not await _is_active_match_callback(message, state):
        return False
    await state.update_data(
        {ACTIVE_MATCH_MESSAGE_ID: None, ACTIVE_MATCH_DETAILS_CLAIM_ID: message.message_id}
    )
    return True


async def _has_match_details_claim(message: Message, state: FSMContext) -> bool:
    data = await state.get_data()
    return (
        data.get(ACTIVE_MATCH_MESSAGE_ID) is None
        and data.get(ACTIVE_MATCH_DETAILS_CLAIM_ID) == message.message_id
    )


async def _restore_match_details_claim(message: Message, state: FSMContext) -> None:
    if not await _has_match_details_claim(message, state):
        return
    await state.update_data(
        {ACTIVE_MATCH_MESSAGE_ID: message.message_id, ACTIVE_MATCH_DETAILS_CLAIM_ID: None}
    )


async def _replace_claimed_match_with_profile_cta(message: Message, state: FSMContext) -> None:
    if not await _has_match_details_claim(message, state):
        return
    await _remove_match_keyboard(message)
    if not await _has_match_details_claim(message, state):
        return
    await _send_match_message(message, state, "🎯 Заполни профиль, чтобы оценить совпадение.", MATCH_PROFILE_CALLBACK)


def format_match_details(match: dict[str, object]) -> str:
    strengths = _reason_values(match.get("strengths"))
    gaps = _reason_values(match.get("gaps"))
    conflicts = _reason_values(match.get("conflicts"))
    lines: list[str] = []
    if strengths:
        lines.extend(["Сильные стороны:", *[f"• {value}" for _, value in strengths[:5]]])
    if gaps:
        if lines:
            lines.append("")
        lines.extend(["Что проверить:", *[f"• {value}" for _, value in gaps[:5]]])
    if conflicts:
        if lines:
            lines.append("")
        lines.extend(["Конфликты:", *[f"• {value}" for _, value in conflicts[:3]]])
    visible_components = {
        component
        for reasons in (strengths[:5], gaps[:5], conflicts[:3])
        for component, _ in reasons
        if component is not None
    }
    other_components = _other_component_values(match, visible_components)
    if other_components:
        if lines:
            lines.append("")
        lines.extend(["Другие критерии:", *other_components])
    return "\n".join(lines) or "Недостаточно данных для объяснения совпадения."


def _other_component_values(match: dict[str, object], visible_components: set[str]) -> list[str]:
    components = match.get("components")
    if not isinstance(components, dict):
        return []
    labels = {
        "seniority": "📈 Опыт",
        "languages": "🌍 Языки",
        "workplace": "🏠 Формат работы",
        "location": "📍 Локация",
        "salary": "💰 Зарплата",
    }
    status_text = {
        "matched": "соответствует",
        "partial": "частичное совпадение",
        "mismatch": "есть расхождение",
        "unknown": "нет данных",
    }
    values: list[str] = []
    for name, label in labels.items():
        if name in visible_components:
            continue
        component = components.get(name)
        if not isinstance(component, dict):
            continue
        status = component.get("status")
        if not isinstance(status, str) or status not in status_text:
            continue
        values.append(f"{label}: {status_text[status]}")
    return values


def _reason_values(value: object) -> list[tuple[str | None, str]]:
    if not isinstance(value, list):
        return []
    values: list[tuple[str | None, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        raw = item.get("value")
        code = item.get("code")
        component = item.get("component")
        component_name = component if isinstance(component, str) else None
        if isinstance(raw, str) and raw:
            values.append((component_name, _format_reason_value(code, raw)))
        elif code == "salary_below_minimum":
            values.append((component_name, "Зарплата ниже указанного минимума"))
    return values


def _format_reason_value(code: object, value: str) -> str:
    templates = {
        "role_matched": "Подходящая роль: {value}",
        "role_partial": "Роль совпадает частично: {value}",
        "role_missing": "Роль для проверки: {value}",
        "required_skills_matched": "Есть обязательный навык: {value}",
        "required_skills_missing": "Не указан обязательный навык: {value}",
        "nice_to_have_skills_matched": "Есть дополнительный навык: {value}",
        "nice_to_have_skills_missing": "Не указан дополнительный навык: {value}",
        "seniority_matched": "Уровень опыта соответствует: {value}",
        "seniority_missing": "Требуемый уровень: {value}",
        "languages_matched": "Язык соответствует: {value}",
        "languages_missing": "Требуемый язык: {value}",
        "workplace_matched": "Формат работы подходит",
        "workplace_missing": "Формат работы для проверки: {value}",
        "location_matched": "Локация соответствует: {value}",
        "location_missing": "Локация для проверки: {value}",
    }
    return templates.get(code, "{value}").format(value=value) if isinstance(code, str) else value


def _error_code(error: httpx.HTTPStatusError) -> str | None:
    try:
        detail = error.response.json().get("detail")
    except (ValueError, AttributeError):
        return None
    return detail.get("code") if isinstance(detail, dict) and isinstance(detail.get("code"), str) else None


async def remove_active_match_inline_keyboard(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    message_id = data.get(ACTIVE_MATCH_MESSAGE_ID) or data.get(ACTIVE_MATCH_DETAILS_CLAIM_ID)
    await state.update_data(
        {ACTIVE_MATCH_MESSAGE_ID: None, ACTIVE_MATCH_DETAILS_CLAIM_ID: None}
    )
    if not isinstance(message_id, int) or message.bot is None:
        return
    try:
        await message.bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=message_id, reply_markup=None)
    except TelegramBadRequest as error:
        if is_message_not_modified(error):
            return
        logger.warning("Could not remove active match inline keyboard", exc_info=True)
    except TelegramAPIError:
        logger.warning("Could not remove active match inline keyboard", exc_info=True)


async def _is_active_match_callback(message: Message, state: FSMContext) -> bool:
    return (await state.get_data()).get(ACTIVE_MATCH_MESSAGE_ID) == message.message_id


async def _remove_match_keyboard(message: Message) -> None:
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as error:
        if is_message_not_modified(error):
            return
        logger.warning("Could not remove match inline keyboard", exc_info=True)
    except TelegramAPIError:
        logger.warning("Could not remove match inline keyboard", exc_info=True)
