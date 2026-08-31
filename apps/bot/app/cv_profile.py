from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from io import BytesIO
from pathlib import PurePath

import httpx
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.api_client import BotApiClient
from app.profile import PROFILE_DRAFT_SOURCE, ProfileSetupStates, profile_payload, show_summary

logger = logging.getLogger(__name__)

MAX_CV_FILE_SIZE = 5 * 1024 * 1024
PDF_MIME_TYPES = {"application/pdf", "application/octet-stream"}
DOCX_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/zip",
    "application/octet-stream",
}

CV_UPLOAD_PROMPT = "Пришли резюме в формате PDF или DOCX размером до 5 МБ.\nДля отмены — /cancel"
CV_UNSUPPORTED_MESSAGE = "Поддерживаются только PDF и DOCX размером до 5 МБ. Пришли другой файл или используй /cancel."
CV_PROCESSING_MESSAGE = "⏳ Обрабатываю CV и готовлю черновик профиля…"
CV_DOWNLOAD_ERROR_MESSAGE = "Не удалось скачать файл из Telegram. Пришли его ещё раз или используй /cancel."
CV_API_ERROR_MESSAGE = "Не удалось подготовить профиль из CV. Попробуй ещё раз или используй /cancel."
CV_PROCESSING_UPLOAD_MESSAGE_ID = "cv_processing_upload_message_id"
CV_TYPING_INTERVAL_SECONDS = 4.0
CV_ERROR_MESSAGES = {
    "file_too_large": CV_UNSUPPORTED_MESSAGE,
    "unsupported_file_type": CV_UNSUPPORTED_MESSAGE,
    "malformed_document": "Не удалось прочитать этот PDF/DOCX. Проверь файл и пришли другой.",
    "no_extractable_text": "В документе не найден текст. Для сканированного CV нужен текстовый PDF или DOCX.",
    "insufficient_job_information": "В CV недостаточно данных, чтобы определить профиль. Пришли другое CV или заполни профиль вручную.",
    "invalid_ai_output": CV_API_ERROR_MESSAGE,
    "ai_provider_error": CV_API_ERROR_MESSAGE,
    "ai_unavailable": CV_API_ERROR_MESSAGE,
    "ai_timeout": "Обработка CV заняла слишком много времени. Попробуй ещё раз.",
    "user_not_found": CV_API_ERROR_MESSAGE,
}


async def handle_cv_profile_setup(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ProfileSetupStates.cv_waiting_document)
    await message.answer(CV_UPLOAD_PROMPT)


async def handle_cv_document(
    message: Message, state: FSMContext, api_client: BotApiClient
) -> None:
    document = message.document
    if document is None or not _supported_document(
        document.file_name, document.mime_type, document.file_size
    ):
        await message.answer(CV_UNSUPPORTED_MESSAGE)
        return
    if message.bot is None or message.from_user is None:
        await message.answer(CV_DOWNLOAD_ERROR_MESSAGE)
        return

    await state.set_state(ProfileSetupStates.cv_processing)
    await state.update_data({CV_PROCESSING_UPLOAD_MESSAGE_ID: message.message_id})
    await message.answer(CV_PROCESSING_MESSAGE)
    try:
        buffer = BytesIO()
        await message.bot.download(document, destination=buffer)
        content = buffer.getvalue()
        if len(content) > MAX_CV_FILE_SIZE:
            raise ValueError("downloaded file exceeds CV limit")
    except (TelegramAPIError, OSError, ValueError):
        logger.warning("Could not download CV document from Telegram", exc_info=True)
        if await _is_active_processing(message, state):
            await _return_to_waiting(state)
            await message.answer(CV_DOWNLOAD_ERROR_MESSAGE)
        return

    if not await _is_active_processing(message, state):
        return

    heartbeat = _CVTypingHeartbeat(message, state)
    await heartbeat.start()
    try:
        user_id = await api_client.create_or_get_user(message.from_user)
        draft = await api_client.create_profile_draft_from_cv(
            user_id,
            filename=document.file_name or "resume",
            content_type=document.mime_type or "application/octet-stream",
            content=content,
        )
    except httpx.HTTPError as error:
        logger.exception("Could not create profile draft from CV through API")
        if await _is_active_processing(message, state):
            await _return_to_waiting(state)
            await message.answer(_api_error_message(error))
        return
    finally:
        await heartbeat.stop()

    if not await _is_active_processing(message, state):
        return
    await state.set_data({**profile_payload(draft), PROFILE_DRAFT_SOURCE: "cv"})
    await show_summary(message, state)


async def handle_unsupported_cv_message(message: Message, state: FSMContext) -> None:
    if await state.get_state() == ProfileSetupStates.cv_waiting_document.state:
        await message.answer(CV_UNSUPPORTED_MESSAGE)


def _supported_document(
    filename: str | None, content_type: str | None, file_size: int | None
) -> bool:
    if file_size is not None and file_size > MAX_CV_FILE_SIZE:
        return False
    suffix = PurePath(filename or "").suffix.casefold()
    mime = (content_type or "application/octet-stream").casefold()
    return (suffix == ".pdf" and mime in PDF_MIME_TYPES) or (
        suffix == ".docx" and mime in DOCX_MIME_TYPES
    )


def _api_error_message(error: httpx.HTTPError) -> str:
    if not isinstance(error, httpx.HTTPStatusError):
        return CV_API_ERROR_MESSAGE
    try:
        detail = error.response.json().get("detail")
    except (ValueError, AttributeError):
        return CV_API_ERROR_MESSAGE
    return CV_ERROR_MESSAGES.get(detail, CV_API_ERROR_MESSAGE) if isinstance(detail, str) else CV_API_ERROR_MESSAGE


async def _is_active_processing(message: Message, state: FSMContext) -> bool:
    if await state.get_state() != ProfileSetupStates.cv_processing.state:
        return False
    return (await state.get_data()).get(CV_PROCESSING_UPLOAD_MESSAGE_ID) == message.message_id


async def _return_to_waiting(state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ProfileSetupStates.cv_waiting_document)


class _CVTypingHeartbeat:
    """Keeps Telegram typing visible only while this upload owns the CV FSM state."""

    def __init__(self, message: Message, state: FSMContext) -> None:
        self._message = message
        self._state = state
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self._send_once()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(CV_TYPING_INTERVAL_SECONDS)
            if not await _is_active_processing(self._message, self._state):
                return
            await self._send_once()

    async def _send_once(self) -> None:
        bot = self._message.bot
        if bot is None:
            return
        try:
            await bot.send_chat_action(chat_id=self._message.chat.id, action="typing")
        except Exception:
            # Typing is best-effort UI feedback and must never interrupt CV processing.
            logger.warning("Could not send CV typing indicator", exc_info=True)
