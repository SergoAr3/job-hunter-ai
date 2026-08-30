# Telegram BOT rules

- Inline-кнопки относятся только к текущему interaction context.
- При переходе пользователя в другой flow или section через главное меню удаляйте активные inline-кнопки предыдущего контекста best-effort.
- Это относится к `Пропустить`, enum/choice-кнопкам, Save/Cancel и section-specific actions.
- Устаревшие inline-кнопки не должны визуально оставаться активными после смены контекста.
- Ошибка Telegram при удалении или редактировании keyboard должна логироваться, но не должна блокировать FSM transition или business logic.
- Для cleanup предпочитайте существующее хранение `message_id`/FSM data; не вводите сложный глобальный UI manager без реальной необходимости.
- При изменениях Telegram routing проверяйте реальный dispatcher-level behavior, а не только прямые вызовы handlers в unit tests.
