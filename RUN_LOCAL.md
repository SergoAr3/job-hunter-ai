# Локальный запуск Job Hunter AI

## Рекомендуемый запуск

Один раз подготовьте окружение из корня репозитория. Существующий `.env` не
перезаписывайте:

```bash
if [ ! -f .env ]; then cp .env.example .env; fi
# Заполните TELEGRAM_BOT_TOKEN и другие необходимые значения в .env.

python3 -m venv apps/api/.venv
apps/api/.venv/bin/pip install -r apps/api/requirements.txt
python3 -m venv apps/bot/.venv
apps/bot/.venv/bin/pip install -r apps/bot/requirements.txt -r apps/bot/requirements-dev.txt
(cd apps/web && npm ci)
```

Нужен Node.js 22.12+ (или совместимая более новая версия), npm и Docker Compose.
Затем запускайте из корня:

```bash
make dev
```

Команда поднимает PostgreSQL, ждёт его готовности, применяет Alembic migrations,
затем запускает FastAPI, Telegram Bot и Next.js Web в dev mode. Web использует
`npm run dev -- -p 3100`, поэтому изменения в `.tsx`, `.ts` и CSS применяются
через Fast Refresh. Зависимости Web автоматически не устанавливаются. Если
`apps/web/node_modules` отсутствует, runner завершится с инструкцией запустить
`cd apps/web && npm install`. Если порт `3100` занят, Next.js сообщит об ошибке,
а runner остановит уже запущенные API/Bot/Web процессы. PostgreSQL при остановке
`make dev` остаётся запущенным.

Проверки и адреса:

- Web Discover: <http://127.0.0.1:3100/discover>
- API: <http://127.0.0.1:8000>
- API health: <http://127.0.0.1:8000/health>
- API Swagger: <http://127.0.0.1:8000/docs>

Нажмите `Ctrl+C`, чтобы завершить API, Bot и Web. Для остановки PostgreSQL
отдельно выполните `docker compose down`.

## Настройка локального Web

Чтобы пользоваться Web, задайте `WEB_DEV_USER_ID` в корневом `.env`. Это
внутренний `users.id` существующего пользователя, **не** Telegram ID. Например,
узнать ID можно запросом к локальной БД:

```bash
docker compose exec db psql -U job_hunter -d job_hunter \
  -c 'SELECT id, telegram_id, username, first_name FROM users ORDER BY id;'
```

Web не создаёт пользователей. Если `WEB_DEV_USER_ID` пуст или отсутствует,
Next.js всё равно запускается и отображает существующую ошибку конфигурации;
укажите ID и перезапустите `make dev`. Server-side `API_BASE_URL` для этого
workflow автоматически указывает на `http://127.0.0.1:8000`. Переменные доступны
только серверному Web-процессу.

Это локальная single-user development configuration, а не production
authentication. Не публикуйте Web/API через tunnel или публичный reverse proxy.

## Smoke-сценарий Discover → Save → Application

Создайте пользователя через `/start` в Telegram. Профиль нужен для matching
preview, но не для поиска или сохранения вакансии.

1. Откройте Discover и выполните поиск.
2. Выберите карточку, затем сохраните вакансию.
3. Откройте сохранённую вакансию и проверьте Application detail.
4. Повторный поиск должен показывать saved badge. Измените статус отклика в BOT
   и обновите detail в Web: статус должен совпасть.

Все данные runtime настоящие; test fixtures используются только в тестах.
Файл `.env` содержит локальные секреты и не должен коммититься.
