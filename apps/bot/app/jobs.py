import logging
from urllib.parse import urlsplit

import httpx
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

logger = logging.getLogger(__name__)

REQUEST_URL_MESSAGE = "Пришли ссылку на вакансию. Для отмены — /cancel"
INVALID_URL_MESSAGE = "Нужна ссылка формата https://… или /cancel"
UNSAFE_URL_MESSAGE = "Эту ссылку нельзя использовать. Пришли другую ссылку или /cancel"
API_UNAVAILABLE_MESSAGE = "Не удалось сохранить вакансию. Пришли ссылку ещё раз или /cancel"
SAVED_MESSAGE = "Вакансия сохранена ✅"
ALREADY_SAVED_MESSAGE = "Эта вакансия уже сохранена."
CANCELLED_MESSAGE = "Добавление вакансии отменено."
NO_ACTIVE_FLOW_MESSAGE = "Нет активного добавления вакансии."


class AddJobStates(StatesGroup):
    waiting_for_url = State()


async def handle_add_job(message: object, state: FSMContext) -> None:
    await state.set_state(AddJobStates.waiting_for_url)
    await message.answer(REQUEST_URL_MESSAGE)


async def handle_cancel(message: object, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer(NO_ACTIVE_FLOW_MESSAGE)
        return

    await state.clear()
    await message.answer(CANCELLED_MESSAGE)


async def handle_job_url(message: object, state: FSMContext, api_client: object) -> None:
    source_url = message.text or ""
    if not _is_basic_http_url(source_url):
        await message.answer(INVALID_URL_MESSAGE)
        return

    telegram_user = message.from_user
    if telegram_user is None:
        return

    try:
        user_id = await api_client.create_or_get_user(telegram_user)
        result = await api_client.save_application(user_id, source_url)
    except httpx.HTTPStatusError as error:
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
        logger.exception("Could not save job through API")
        await message.answer(API_UNAVAILABLE_MESSAGE)
        return

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


def _is_basic_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def format_job_card(job: dict[str, object]) -> str:
    salary = job.get("salary_text") or _format_structured_salary(job)
    fields = [("Вакансия", job.get("title")), ("Компания", job.get("company")), ("Локация", job.get("location")), ("Формат", job.get("workplace_type")), ("Тип занятости", job.get("employment_type")), ("Зарплата", salary)]
    lines = [f"{label}: {value}" for label, value in fields if value and value != "unknown"]
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
    return " ".join(part for part in (amount, currency, f"per {period}" if period and period != "unknown" else None) if part)
