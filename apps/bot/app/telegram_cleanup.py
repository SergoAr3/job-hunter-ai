from aiogram.exceptions import TelegramBadRequest


def is_message_not_modified(error: TelegramBadRequest) -> bool:
    return "message is not modified" in str(error).casefold()
