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

- Web: <http://127.0.0.1:3100/>
- API: <http://127.0.0.1:8000>
- API health: <http://127.0.0.1:8000/health>
- API Swagger: <http://127.0.0.1:8000/docs>

Нажмите `Ctrl+C`, чтобы завершить API, Bot и Web. Для остановки PostgreSQL
отдельно выполните `docker compose down`.

## Auth и локальный Web после Slice 5

`make dev` использует те же правила идентификации, что production: Web требует
настоящую AuthSession. Откройте <http://127.0.0.1:3100/> — без session вы попадёте
на `/login`; после входа — в workspace. Никакой заранее настроенный users.id
не нужен, users/credentials автоматически не создаются.

1. Запустите `make dev`.
2. Зарегистрируйтесь по email на `/register` и войдите на `/login`, либо нажмите
   «Войти через Telegram» и подтвердите свой код в Bot.
3. Работайте с Profile, Discover и Applications.

Если у вас уже есть данные Bot, используйте Telegram login: он откроет тот же
аккаунт с прежними Profile / Applications / history. Email account открывает
свои данные; автоматического объединения аккаунтов нет. Регистрация возвращает
нейтральный результат и не создаёт session или Profile.

После logout session отзывается (либо сообщается о неподтверждённом отзыве при
outage), local cookie удаляется, protected pages снова требуют login. Invalid,
expired и revoked sessions не предоставляют доступ. Недоступный API отображает
unavailable UX и сохраняет cookie; после recovery действующая session продолжает
работать с тем же аккаунтом.

Для API и Bot runner использует одинаковый `BOT_API_SERVICE_TOKEN` из `.env`
либо генерирует временный случайный secret на один запуск. Он не записывается
в файл и не передаётся Web. Для самостоятельного запуска задайте один и тот же
server-only secret обоим процессам (минимум 32 printable ASCII characters без
пробелов). Production отвергает placeholder/test secrets. `/users/telegram`
остаётся Bot-only. User-scoped API требует Bearer session или Bot credential.

В local HTTP используется HttpOnly cookie `job_hunter_session_dev`, SameSite=Lax,
Path=/, без Secure. Production использует Secure `__Host-job_hunter_session`.
Для отдельного Web процесса задайте серверный `API_BASE_URL` в
`apps/web/.env.local`. Cookie/environment различия не дают дополнительных прав.

В production задайте точный `WEB_PUBLIC_ORIGIN=https://your-host.example`
(без trailing slash). Host/X-Forwarded-Host не определяют доверенный Origin.
Локально разрешены `http://127.0.0.1:3100`, `http://localhost:3100` и явно настроенный
`WEB_PUBLIC_ORIGIN`. Не добавляйте реальные secrets в examples или Git.

Текущий контракт: [docs/auth-slice5.md](docs/auth-slice5.md). Email verification,
password reset и public rollout hardening остаются Slice 6.

## Smoke-сценарий Discover → Save → Application

Войдите по email или Telegram в Web. Профиль нужен для matching
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
