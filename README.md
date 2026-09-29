# Job Hunter AI

Job Hunter AI — ассистент для поиска работы, который помогает сохранять и структурировать вакансии, отслеживать отклики и подбирать наиболее подходящие позиции на основе опыта и навыков пользователя.

## Архитектура и структура

Бизнес-логика и доступ к PostgreSQL находятся в FastAPI. Telegram-бот работает
с данными только через API.

- `apps/api` — FastAPI, SQLAlchemy models, services и Alembic migrations;
- `apps/bot` — Telegram-бот на aiogram;
- `apps/web` — Next.js Web MVP;
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

Подготовьте `.env` и локальные зависимости по инструкции в
[RUN_LOCAL.md](RUN_LOCAL.md), затем запустите из корня:

```bash
make dev
```

API reloads after Python-code changes through Uvicorn. BOT reloads after Python
changes under `apps/bot/app` through the development dependency `watchfiles`;
the dev runner stops the old polling process before starting its replacement.
`make dev` также запускает Web в Next.js dev mode с Fast Refresh. Команда
поднимает PostgreSQL, применяет Alembic migrations и запускает FastAPI, Telegram
BOT и Web с префиксированными логами. Web: [`http://127.0.0.1:3100/discover`](http://127.0.0.1:3100/discover).
`Ctrl+C` останавливает API/BOT/Web и оставляет PostgreSQL запущенным.

## Текущий scope и roadmap

Текущий scope покрывает ручное сохранение и enrichment вакансий, applications,
UserProfile v1 и подтверждаемый пользователем AI profile draft из CV.
Matching/ranking, relational skill taxonomy и i18n (выбор/смена языка,
локализованные messages и buttons) остаются следующими отдельными этапами.

## Web MVP: Discover → Save → Application

`apps/web` — Next.js App Router + TypeScript, второй thin client существующего
API. Первый slice включает поиск в «Работа России / RU», просмотр результата,
matching preview, сохранение и read-only карточку Application. Applications list
и изменение статуса пока доступны только в Telegram.

Для локального запуска нужен Node.js 22.12+ (или совместимый более новый Node.js),
npm и существующий пользователь из BOT. Профиль нужен для matching preview, но не
для поиска/сохранения. URL, настройка `WEB_DEV_USER_ID` и ограничения локального
single-user режима описаны в [RUN_LOCAL.md](RUN_LOCAL.md).

Browser обращается к same-origin Web transport, который подставляет user ID;
бизнес-логика остаётся в FastAPI. CORS для этого flow не нужен. Search не сохраняет
вакансии, Save передаёт только source identity. Вакансия, уже сохранённая до поиска,
показывает badge без повторного Save ради получения ID.

Проверки из `apps/web`: `npm test`, `npm run lint`, `npm run typecheck`,
`npm run build`. Production smoke: `npm run start` после build. Тестовые fixtures
используются только тестами; runtime обращается к реальному API.
