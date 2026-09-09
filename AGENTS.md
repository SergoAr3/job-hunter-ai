# Правила работы AI-агентов

- Сначала изучайте существующий код и документацию, относящиеся к задаче.
- Перед крупными изменениями предлагайте план и дождитесь согласования, если
  пользователь не поручил немедленную реализацию.
- Не изменяйте файлы вне границ поставленной задачи.
- Не добавляйте зависимости без явного объяснения необходимости.
- Размещайте бизнес-логику в API, а не в Telegram handlers или web-приложении.
- Bot и web должны обращаться к API; не дублируйте в них бизнес-логику и доступ
  к данным.
- Никогда не коммитьте секреты: токены, ключи, пароли и файлы `.env`.
- Сопровождайте новый функционал релевантными тестами.
- Предпочитайте простые решения преждевременным абстракциям и инфраструктуре
  «на будущее».

## Project-specific review

- При изменениях БД/SQLAlchemy проверяйте согласованность моделей и миграций,
  nullable/defaults/constraints/foreign keys, transaction boundaries,
  commit/rollback/savepoints и состояние session после exceptions, concurrency,
  legacy data/backfill, upgrade/downgrade и различия PostgreSQL и SQLite.
- При изменениях bot/client проверяйте thin-client boundary, error UX,
  nullable values, API failures, Telegram message limits, state transitions
  и retry/cancel behavior. Для Telegram routing и lifecycle соблюдайте
  `apps/bot/AGENTS.md`.
