# Job Hunter AI

Job Hunter AI — персональное пространство для поиска вакансий, оценки их релевантности и ведения откликов.

## Что умеет сейчас

### Discover

- Поиск вакансий в «Работа России» (Trudvsem) для рынка RU через Web и Telegram.
- Просмотр результатов и деталей вакансии, предварительная оценка совпадения с профилем пользователя.
- Сохранение вакансии в Applications и переход к её карточке. При сохранении API получает детали из источника и приводит их к общей модели вакансии.

### Applications

- Web-раздел `/applications`: сохранённые вакансии, поиск по названию или компании, фильтр по статусу, сортировка и пагинация.
- Карточка `/applications/{applicationId}`: детали вакансии, изменение статуса и история статусов, загружаемая при раскрытии блока. Web подтверждает изменение повторным чтением из API после `PUT`.
- В Telegram доступны список откликов и работа со статусами; API также хранит заметки и следующие действия по откликам.

### Профиль и Telegram

- Профиль пользователя с ручным заполнением и подтверждаемым AI-черновиком из PDF/DOCX резюме; matching использует данные профиля и вакансии.
- Telegram-бот на aiogram поддерживает Discover, сохранение вакансий и ведение откликов. Бот обращается к API, не работает с БД напрямую.

## Архитектура

```text
Telegram → Bot ─┐
                ├→ API ─┬→ PostgreSQL
Browser  → Web ─┘       ├→ OpenAI API
                        └→ Trudvsem
```

`apps/api` содержит бизнес-логику, matching, интеграции с источниками и доступ к данным. `apps/bot` и `apps/web` — клиенты API; Web использует серверный транспорт Next.js. Миграции БД находятся в `apps/api/alembic`, проектная документация — в `docs`.

Matching Foundation v1 добавляет внутренний derived слой `Skill`/`SkillAlias` и
связи `UserSkill`/`JobSkill` поверх сохранённых semantic JSON skills. Profile PUT,
CV Apply и Job AI enrichment синхронизируют связи атомарно. Текущий matcher
`job-match-v2.1` продолжает читать прежние поля; Match UI ещё не добавлен.
[Matching Foundation v1](docs/matching-foundation-v1.md) описывает normalization,
migration, bounded backfill и rollback.

## Технологии

Python, FastAPI, SQLAlchemy, Alembic, PostgreSQL, aiogram, Next.js, React, TypeScript, OpenAI API и Docker Compose. Для проверок используются pytest, Vitest и React Testing Library.

## Локальный запуск

Основной способ — `make dev` из корня репозитория. Подготовьте Docker Compose, Python, Node.js 22.12+ и npm; создайте корневой `.env` по `.env.example`, установите зависимости API, Bot и Web по [инструкции локального запуска](RUN_LOCAL.md). Для Telegram нужен `TELEGRAM_BOT_TOKEN`; для функций с OpenAI — `OPENAI_API_KEY`.

Откройте Web и войдите по email/password после подтверждения email или через Telegram. `make dev` сохраняет verification/reset links в private dev mail capture; recovery flow описан в [RUN_LOCAL.md](RUN_LOCAL.md). Для доступа к существующим данным Bot используйте Telegram login: он открывает тот же аккаунт. `make dev` задаёт `API_BASE_URL=http://127.0.0.1:8000` для Bot и Web; внутренний users.id настраивать не нужно.

```bash
make dev
```

Команда поднимает PostgreSQL, применяет миграции Alembic и запускает API, Bot и Web. Web: [Discover](http://127.0.0.1:3100/discover) и [Applications](http://127.0.0.1:3100/applications); API и Swagger: [API](http://127.0.0.1:8000) и [Swagger](http://127.0.0.1:8000/docs). `Ctrl+C` останавливает приложения; PostgreSQL остаётся запущенным. Подробности настройки и остановки БД — в [RUN_LOCAL.md](RUN_LOCAL.md).

Проверки: из `apps/api` и `apps/bot` — `.venv/bin/python -m pytest tests`; из `apps/web` — `npm test`, `npm run lint`, `npm run typecheck`, `npm run build`.

## Статус проекта и планы

Проект активно развивается. Web MVP с Discover и Applications и Telegram-сценарии уже работают; центральный слой для них — API. Поиск Discover сейчас подключён к «Работа России» для RU. Поддержка нескольких рынков и источников ещё в разработке.

Ближайшие направления: подготовка GitHub-страницы и демо, официальный доступ к источникам вакансий Армении, семантика нескольких рынков и интеграции Армении, международные remote-источники и ATS компаний, улучшение качества Discover и matching v2, развитие учёта откликов и follow-up. Это планы, а не доступные интеграции или функции.
