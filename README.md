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
- Локальная инфраструктура: Docker Compose для PostgreSQL

## Первый вертикальный срез

Реализован минимальный путь `Telegram /start → bot → API → PostgreSQL`:

- `GET /health` возвращает `{"status": "ok"}`;
- `POST /users/telegram` создаёт пользователя Telegram или возвращает уже
  существующего, актуализируя его профиль;
- бот передаёт данные пользователя в API и после успешного ответа отправляет
  приветствие.

## Локальный запуск

1. Скопируйте `.env.example` в `.env` и задайте `TELEGRAM_BOT_TOKEN`.
2. Запустите PostgreSQL: `docker compose up -d db`.
3. В первом терминале запустите API:

   ```sh
   cd apps/api
   python -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   DATABASE_URL=postgresql+psycopg://job_hunter:job_hunter@localhost:5432/job_hunter alembic upgrade head
   DATABASE_URL=postgresql+psycopg://job_hunter:job_hunter@localhost:5432/job_hunter uvicorn app.main:app --reload
   ```

4. В другом терминале запустите bot:

   ```sh
   cd apps/bot
   python -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   set -a; . ../../.env; set +a
   API_BASE_URL=http://localhost:8000 python -m app.main
   ```

API будет доступен по адресу `http://localhost:8000`; проверка состояния —
`http://localhost:8000/health`.
