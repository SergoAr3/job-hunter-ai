# Запуск Job Hunter AI локально

## Что запускаем

Локально проект состоит из трёх частей:

1. PostgreSQL — база данных.
2. API — FastAPI backend.
3. BOT — Telegram bot.

Схема:

```text
Telegram -> BOT -> API -> PostgreSQL
```

---

## 1. Запуск PostgreSQL

Из корня проекта:

```bash
docker compose up -d db
```

Проверить:

```bash
docker compose ps
```

Ожидаемо PostgreSQL должен быть `healthy`, а порт должен быть проброшен примерно так:

```text
0.0.0.0:5432->5432/tcp
```

---

## 2. Запуск API

Открыть отдельный терминал:

```bash
cd apps/api
source .venv/bin/activate
```

Проверить Python:

```bash
which python
python --version
```

`python` должен указывать на:

```text
apps/api/.venv/bin/python
```

Если зависимости ещё не установлены:

```bash
python -m pip install -r requirements.txt
```

### Применить миграции

```bash
DATABASE_URL=postgresql+psycopg://job_hunter:job_hunter@localhost:5432/job_hunter python -m alembic upgrade head
```

### Запустить API

```bash
DATABASE_URL=postgresql+psycopg://job_hunter:job_hunter@localhost:5432/job_hunter python -m uvicorn app.main:app --reload
```

API будет доступен:

```text
http://127.0.0.1:8000
```

Проверка:

```text
http://127.0.0.1:8000/health
```

Ожидаемый ответ:

```json
{"status":"ok"}
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

Не закрывать этот терминал, пока нужен API.

---

## 3. Запуск Telegram BOT

Открыть второй терминал:

```bash
cd apps/bot
```

Если `.venv` ещё нет:

```bash
python3 -m venv .venv
```

Активировать:

```bash
source .venv/bin/activate
```

Если зависимости ещё не установлены:

```bash
python -m pip install -r requirements.txt
```

В корневом `.env` должен быть Telegram token:

```env
TELEGRAM_BOT_TOKEN=your_real_token
```

Загрузить `.env`:

```bash
set -a
source ../../.env
set +a
```

Запустить bot:

```bash
API_BASE_URL=http://127.0.0.1:8000 python -m app.main
```

После запуска отправить боту в Telegram:

```text
/start
```

Бот должен ответить приветствием.

---

## 4. Проверка всей цепочки

После `/start` должно происходить:

```text
Telegram
   ↓
BOT
   ↓ POST /users/telegram
API
   ↓
PostgreSQL
   ↓
ответ API
   ↓
BOT
   ↓
приветствие пользователю
```

Проверить пользователя в PostgreSQL:

```bash
docker compose exec db psql -U job_hunter -d job_hunter
```

Внутри `psql`:

```sql
SELECT * FROM users;
```

Выйти:

```sql
\q
```

Повторный `/start` не должен создавать второго пользователя с тем же `telegram_id`.

---

## 5. Как остановить проект

API и BOT:

```text
Ctrl+C
```

PostgreSQL:

```bash
docker compose down
```

Если базу нужно просто остановить без удаления контейнеров:

```bash
docker compose stop db
```

---

## Быстрый порядок запуска

### Терминал 1 — PostgreSQL

Из корня:

```bash
docker compose up -d db
```

### Терминал 2 — API

```bash
cd apps/api
source .venv/bin/activate
DATABASE_URL=postgresql+psycopg://job_hunter:job_hunter@localhost:5432/job_hunter python -m alembic upgrade head
DATABASE_URL=postgresql+psycopg://job_hunter:job_hunter@localhost:5432/job_hunter python -m uvicorn app.main:app --reload
```

### Терминал 3 — BOT

```bash
cd apps/bot
source .venv/bin/activate
set -a
source ../../.env
set +a
API_BASE_URL=http://127.0.0.1:8000 python -m app.main
```

После этого можно пользоваться ботом.
