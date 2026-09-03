# Job Hunter AI

Job Hunter AI — Telegram-ассистент для сохранения и структурирования вакансий,
ведения откликов и подготовки данных пользователя для будущего matching.

## Архитектура и структура

Бизнес-логика и доступ к PostgreSQL находятся в FastAPI. Telegram-бот работает
с данными только через API.

- `apps/api` — FastAPI, SQLAlchemy models, services и Alembic migrations;
- `apps/bot` — Telegram-бот на aiogram;
- `apps/web` — заготовка будущей web-панели;
- `docs` — проектная документация.

Стек: Python, FastAPI, aiogram, SQLAlchemy, PostgreSQL, Alembic и OpenAI API.

## Что работает

- Telegram `/start` с идемпотентным созданием пользователя;
- `/add_job` и сохранение связки `Job` / `Application`;
- deterministic parsing поддерживаемых страниц вакансий;
- AI enrichment вакансий через OpenAI;
- PostgreSQL schema management через Alembic;
- UserProfile v1 с ручной настройкой через `/profile_setup` и AI draft из PDF/DOCX CV.

## Локальный запуск

### Одной командой

После создания `.env` и обоих project virtual environments запустите:

```sh
make dev
```

Для BOT auto-reload установите его development dependencies один раз:

```sh
cd apps/bot
.venv/bin/python -m pip install -r requirements-dev.txt
```

Команда поднимает PostgreSQL (`docker compose up -d db`), применяет Alembic
migrations и запускает API с `--reload`, затем Telegram BOT с
`API_BASE_URL=http://127.0.0.1:8000`. API и BOT работают параллельно, а их
объединённые stdout/stderr logs получают префиксы `[API]` и `[BOT]`. `Ctrl+C`
останавливает оба процесса, но оставляет PostgreSQL запущенным.

API reloads after Python-code changes through Uvicorn. BOT reloads after Python
changes under `apps/bot/app` through the development dependency `watchfiles`;
the dev runner stops the old polling process before starting its replacement.
Migrations run once before either runtime process starts.

1. Скопируйте `.env.example` в `.env` и задайте конфигурацию:

   ```sh
   TELEGRAM_BOT_TOKEN=...
   OPENAI_API_KEY=...
   OPENAI_MODEL=gpt-5-mini
   OPENAI_TIMEOUT_SECONDS=15
   CV_AI_TIMEOUT_SECONDS=30
   ```

2. Запустите PostgreSQL:

   ```sh
   docker compose up -d db
   ```

3. Установите зависимости и запустите API:

   ```sh
   cd apps/api
   python -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   set -a; . ../../.env; set +a
   alembic upgrade head
   uvicorn app.main:app --reload
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

API по умолчанию доступен на `http://localhost:8000`; health check —
`GET /health`.

## Текущий scope и roadmap

Текущий scope покрывает ручное сохранение и enrichment вакансий, applications,
UserProfile v1 и подтверждаемый пользователем AI profile draft из CV.
Matching/ranking, relational skill taxonomy, Web UI и i18n (выбор/смена языка,
локализованные messages и buttons) остаются следующими отдельными этапами.
