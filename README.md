# Job Hunter AI

Job Hunter AI — будущий AI-ассистент для поиска работы. Проект находится на
этапе начальной подготовки репозитория: продуктовые функции и интеграции пока
не реализованы.

## Структура

- `apps/web` — будущая web-панель на Next.js и TypeScript.
- `apps/api` — будущий backend API на FastAPI; здесь будет располагаться
  бизнес-логика и доступ к данным.
- `apps/bot` — будущий Telegram-бот на aiogram; он будет обращаться к API.
- `docs` — короткая документация по решениям проекта по мере необходимости.

## Стек

- Web: Next.js + TypeScript
- API: FastAPI + Python
- Bot: aiogram + Python
- Database: PostgreSQL
- ORM: SQLAlchemy
- Migrations: Alembic
- Локальная инфраструктура: Docker Compose (будет добавлена с первым
  вертикальным срезом)

## Текущий статус

Репозиторий содержит только минимальную структуру. Зависимости, Docker Compose,
схема базы данных, OpenAI и Telegram Bot API пока не подключены.
