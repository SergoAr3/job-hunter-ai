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

## Auth Slice 2: локальный trust boundary

Обновите API-зависимости (`apps/api/.venv/bin/pip install -r apps/api/requirements.txt`):
password hashing использует Argon2id. `make dev` явно задаёт API
`APP_ENV=development`, `AUTH_ROLLOUT_MODE=legacy-development` и слушает loopback.
User-scoped routes требуют session/Bot credential либо отдельный dev secret.
Runner генерирует временный `WEB_DEV_API_TOKEN` для API и Next server,
ограниченный одним `WEB_DEV_USER_ID`; browser его не получает. Peer IP должен
быть loopback; Uvicorn не доверяет forwarded headers. Неверный Bearer всегда
отклоняется без fallback на dev credential.
`/users/telegram` требует service credential даже в этом режиме.

Для API и Bot runner использует одинаковый `BOT_API_SERVICE_TOKEN` из `.env`
либо генерирует временный случайный secret на один запуск. Он не записывается
в файл и не передаётся Web. Для самостоятельного запуска API/Bot задайте одинаковый
server-only secret (минимум 32 printable ASCII characters без пробелов).
API по умолчанию запускается с `APP_ENV=production`, `AUTH_ROLLOUT_MODE=enforced`:
без service credential startup завершается ошибкой, user-scoped routes требуют
session или Bot credential. Production запрещает `legacy-development` и
`WEB_DEV_API_TOKEN`, отвергает известные placeholder/test/dev secrets.
Сгенерируйте production Bot secret и передайте его обоим процессам через secret storage:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Auth остаётся private/dev flow: email verification будет в Slice 6. Backend
контракт: [docs/auth-slice2.md](docs/auth-slice2.md); текущий Web flow:
[docs/auth-slice3.md](docs/auth-slice3.md).

## Настройка локального Web

### A. Transitional dev identity (до Slice 5)

Чтобы пользоваться Web без login, задайте `WEB_DEV_USER_ID` в корневом `.env`. Это
внутренний `users.id` существующего пользователя, **не** Telegram ID. Например,
узнать ID можно запросом к локальной БД:

```bash
docker compose exec db psql -U job_hunter -d job_hunter \
  -c 'SELECT id, telegram_id, username, first_name FROM users ORDER BY id;'
```

Dev fallback не создаёт пользователей. Если `WEB_DEV_USER_ID` пуст или отсутствует,
Next.js всё равно запускается и отображает существующую ошибку конфигурации;
укажите ID и перезапустите `make dev`. Server-side `API_BASE_URL` для этого
workflow автоматически указывает на `http://127.0.0.1:8000`. Переменные доступны
только серверному Web-процессу.

Это локальная single-user development configuration, а не production
authentication. Не публикуйте Web/API через tunnel или публичный reverse proxy.

### B. Real email login

Откройте `http://127.0.0.1:3100/register`, отправьте форму, затем войдите на
`/login`. Регистрация возвращает нейтральный результат и не создаёт сессию
или Profile. Для нового аккаунта создайте Profile через Web. `WEB_DEV_USER_ID`
не нужен для real session: cookie имеет приоритет над dev identity во всех
domain requests. Невалидная/expired cookie не включает fallback.

В local HTTP используется отдельная HttpOnly cookie `job_hunter_session_dev`.
После logout без cookie режим A может снова показать dev account; это явный
transitional workflow, который будет удалён в Slice 5. Для проверки redirect
без cookie запускайте Next с `AUTH_ROLLOUT_MODE=enforced`.

В production задайте точный `WEB_PUBLIC_ORIGIN=https://your-host.example`
(без trailing slash). Host/X-Forwarded-Host не определяют доверенный Origin.
Локально разрешены только `http://127.0.0.1:3100`, `http://localhost:3100` и
явно настроенный `WEB_PUBLIC_ORIGIN`. Не меняйте `.env.example` на реальные секреты.

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

## Telegram Web login и linking (Slice 4)

Укажите публичный `TELEGRAM_BOT_USERNAME` без `@` в корневом `.env` для API
(например имя из Bot profile), затем перезапустите `make dev`. `TELEGRAM_BOT_TOKEN`
и Bot service credential остаются только у API/Bot, не передаются Web.
Новая migration `20261002_16` создаёт только короткоживущие challenges.

На `/login` нажмите «Войти через Telegram»: вкладка Telegram откроется автоматически.
Web останется в ожидании; если открытие заблокировано, используйте запасную ссылку
«Открыть Telegram». Сравните шестизначный
код Web/Bot. Подтверждайте только собственный запрос. После подтверждения Web
завершит вход в том же браузере: старый Telegram account сохраняет свой users.id,
Profile и Applications. Новый Telegram получает account без Profile.
Если передумали, нажмите «Отменить вход» в Web: polling остановится, waiting
исчезнет и challenge cookie очистится; можно сразу начать новую попытку.

В account shell email-пользователя «Подключить Telegram» требует повторного
ввода пароля и подтверждения в Bot. Занятый другим account Telegram вызывает
явный конфликт: никакого merge/переноса. Отменённый/expired/consumed challenge
не переиспользуется; начните новый. Каждая attempt имеет отдельную HttpOnly
binding cookie: terminal response одной вкладки не удаляет binding другой.
Незавершённые cookies истекают через пять минут. AuthSession по-прежнему общая
для вкладок; login при уже активной session не разрешён. После Bot restart
откройте deep link снова.
Подробный контракт: [docs/auth-slice4.md](docs/auth-slice4.md).
