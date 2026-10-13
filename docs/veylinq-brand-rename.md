# Veylinq: отчёт о переименовании

Дата: 2026-10-14. Ветка: `chore/veylinq-brand-rename`.

## 1. Что изменено

Выполнен точечный branding rename без изменения описания и смысла продукта,
архитектуры, API semantics, matching, auth или feature scope. Новых зависимостей
и миграций нет. Исторические миграции, schema objects, persisted values, маршруты,
callback payloads, environment variable names и Git history не изменены.
Production/default matcher остаётся `job-match-v2.1`; версии `job-match-v2.2`
и `matcher-identity-v1`, frozen alias policy, shadow comparator, snapshots и
fallback не затронуты. Commit, push, PR, merge и rename GitHub repo не выполнялись.

## 2. User-facing места

- Web: общий page title, sidebar (Veylinq / VL), login/register,
  email verification и password recovery, тексты reminder drafts/read states.
  Общий layout распространяет title на dashboard, applications, discover,
  profile и CV import; отдельных старых заголовков там не найдено.
- API/OpenAPI/Swagger: `Veylinq API`.
- SMTP: темы подтверждения email и сброса пароля.
- Telegram: подтверждение входа; связанные fixtures и dispatcher test ожидания.
- README, локальный запуск и документация auth/reminders.

Слоганы и metadata description сохранены. В start/help, error/loading/empty states,
alt text, конфигурации и прочих экранах дополнительных старых brand strings нет.
Отдельных brand favicon/logo/static images, SEO/OG brand fields, GitHub templates,
Python package metadata или deployment manifests с брендом в tracked files нет.
Новые визуальные assets и OG/SEO поля не добавлялись.

## 3. Safe technical identifiers

- Приватный npm package: `veylinq-web`; в lockfile изменены только два name поля.
- User-Agent обоих HTTP transports: `Veylinq/1.0`; запросы и обработка не изменены.
- Readiness check dev-скрипта теперь ищет `<title>Veylinq</title>`.

## 4. Намеренно оставленные legacy identifiers

| Группа | Идентификаторы | Категория |
| --- | --- | --- |
| БД | `job_hunter` в config, Alembic connection example, Compose и pg_isready | C |
| Auth | `job_hunter_session_dev`, `__Host-job_hunter_session` | C |
| Telegram browser binding | `job_hunter_telegram_dev_<token>`, `__Host-job_hunter_telegram_<token>` и invalid-cookie fixture | C |
| CV imports | `job-hunter-cv-imports-<uid>` | C |
| Dev mail capture | `/tmp/job-hunter-auth-mail` | C |
| Python client | `JobHunterApiClient`, его import/call sites и tests | C |
| Git/Compose identity | имя локальной папки/repo, origin, существующий Compose project/container/volume | C/D |

## 5. Почему оставлены

Переименование БД/роли/Compose project может оторвать существующие данные и volume.
Изменение cookies разлогинит пользователей и нарушит pending Telegram challenges.
Смена CV import directory потеряет доступ к незавершённым imports; смена mail capture
directory нарушит текущий recovery workflow и инструкции. Python class — внутренний
import contract; косметическая смена не нужна для видимого бренда. Документация и
тесты этих идентификаторов продолжают описывать реальное поведение.

## 6. Остатки старого naming

Повторно проверены все запрошенные варианты, дополнительные `job-hunter-web`,
`job_hunter`, `JobHunterApiClient`, `Job Hunter Bot` и переносы строк между словами.
В исходниках и документации вне этого отчёта нет старых user-facing названий,
product URL, JH или JHAI. `job_hunter_ai`, `jobhunter.ai`, `app.jobhunter.ai` не найдены.
Ни localhost, ни third-party URL не менялись.

Ниже — полный список остатков в tracked files: 94 совпадения на 86 строках
(число совпадений включает повторные DB credentials в одной строке). Все относятся
к категориям C выше. Старые имена в этом отчёте намеренны: это compatibility inventory.
`.git` history/config не переписывались. Игнорируемый `.env` содержит legacy naming
на строке 8; значения не раскрывались, файл не менялся. `.venv`, node_modules и
генерируемые caches не редактировались ради бренда; они не являются source artifacts.

## 7. Проверки

| Проверка | Результат |
| --- | --- |
| API: `.venv/bin/python -m pytest tests -q` | 1582 passed, 97 skipped |
| Bot: `.venv/bin/python -m pytest tests -q` | 527 passed |
| Web: `npm test` | 750 passed, 47 files |
| Web: `npm run lint` | passed |
| Web: `npm run typecheck` | passed |
| Web: `npm run build` | passed, production build |
| API/Bot Pyright, существующие конфиги | failed: 46 / 57 errors |
| `bash scripts/tests/test_dev_cleanup.sh` | passed, все 4 сценария |
| `bash -n scripts/dev.sh` | passed |
| `git diff --check` | passed |

Pyright diagnostics проверены против `HEAD`: текст каждого диагностируемого диапазона
идентичен исходному. Ошибки находятся вне строк переименования; исправления типов
не включались в branding scope. Логи diagnostics сохранены локально в
`/tmp/veylinq-rename-{api,bot}-pyright.json`. Отдельного Python lint script/CI в repo нет.
Web tests проверяют auth brand/sidebar initials и reminder copy; SMTP test теперь
проверяет обе темы писем. Production HTML/RSC проверены на отсутствие старого бренда.

## 8. Runtime behavior

Перед проверками изучены процессы/PID и listening ports. Web (:3100), API (:8000),
Bot и reminder worker/poller не запущены. Уже работающий PostgreSQL:
`job-hunter-ai-db-1`, postgres:16-alpine, healthy, порт 5432. Контейнер сохранён;
второй stack/контейнер/сервер не запускался. Проверки используют SQLite и mocks;
существующая БД не мигрировалась и не менялась. PostgreSQL opt-in проверки не включены;
97 API tests пропущены по условиям suite (включая opt-in PostgreSQL проверки).

Дополнительно выполнялись только конечные pytest/Vitest/lint/typecheck/build и
существующий cleanup regression script. Последний создал временные dummy-процессы
и остановил все их группы (SIGTERM, SIGINT, child failure, startup race).
Временный test runtime завершён; исходный PostgreSQL оставлен запущенным.
Live browser/Bot/SMTP/provider smoke и production deployment не выполнялись.

## 9. Manual follow-up

- GitHub repo rename и затем обновление git remote, ссылок и CI, если будет отдельно разрешено.
  Текущий origin сохранён: `https://github.com/SergoAr3/job-hunter-ai.git`.
- Перед переименованием локальной папки/Compose project сохранить существующую
  привязку к контейнеру и persistent volume; не создавать новую пустую БД.
- Проверить владение/настройку `veylinq.com` и `app.veylinq.com`, production DNS,
  TLS и deployment; выставить доверенный `WEB_PUBLIC_ORIGIN=https://app.veylinq.com`
  в production и согласовать mail sender/SMTP secrets. Repo не содержит старых
  product URL, которые требовали бы замены; владение новыми доменами не проверялось.
- Telegram BotFather: name/about/description, avatar и при необходимости username;
  затем обновить deployment `TELEGRAM_BOT_USERNAME` и внешние deep links.
- Проверить внешние integrations/provider registrations и User-Agent allowlists,
  OAuth/app registrations, monitoring/analytics service names и Staff.am materials,
  если они существуют. В репозитории соответствующих branding settings не найдено.
- Secrets и `.env` обновлять отдельно при реальной смене инфраструктуры; массового
  переименования secrets/credentials не требуется.

## Приложение: точные locations остатков

Формат: путь:строка — найденные legacy tokens; соответствующая причина описана в §4–5.

- `RUN_LOCAL.md:83` — `job_hunter`
- `RUN_LOCAL.md:84` — `job_hunter`
- `RUN_LOCAL.md:101` — `job-hunter`
- `apps/api/alembic.ini:4` — `job_hunter`, `job_hunter`, `job_hunter`
- `apps/api/app/config.py:5` — `job_hunter`, `job_hunter`, `job_hunter`
- `apps/api/app/services/cv_import_state.py:117` — `job-hunter`
- `apps/bot/app/api_client.py:234` — `JobHunter`
- `apps/bot/app/api_client.py:585` — `JobHunter`
- `apps/bot/app/main.py:10` — `JobHunter`
- `apps/bot/app/main.py:49` — `JobHunter`
- `apps/bot/tests/test_api_client.py:7` — `JobHunter`
- `apps/bot/tests/test_api_client.py:35` — `JobHunter`
- `apps/bot/tests/test_api_client.py:62` — `JobHunter`
- `apps/bot/tests/test_api_client.py:85` — `JobHunter`
- `apps/bot/tests/test_api_client.py:112` — `JobHunter`
- `apps/bot/tests/test_api_client.py:125` — `JobHunter`
- `apps/bot/tests/test_api_client.py:148` — `JobHunter`
- `apps/bot/tests/test_api_client.py:189` — `JobHunter`
- `apps/bot/tests/test_api_client.py:213` — `JobHunter`
- `apps/bot/tests/test_api_client.py:241` — `JobHunter`
- `apps/bot/tests/test_api_client.py:265` — `JobHunter`
- `apps/bot/tests/test_api_client.py:296` — `JobHunter`
- `apps/bot/tests/test_api_client.py:315` — `JobHunter`
- `apps/bot/tests/test_api_client.py:333` — `JobHunter`
- `apps/bot/tests/test_api_client.py:352` — `JobHunter`
- `apps/bot/tests/test_api_client.py:372` — `JobHunter`
- `apps/bot/tests/test_api_client.py:404` — `JobHunter`
- `apps/bot/tests/test_api_client.py:466` — `JobHunter`
- `apps/bot/tests/test_api_client.py:492` — `JobHunter`
- `apps/bot/tests/test_api_client.py:536` — `JobHunter`
- `apps/bot/tests/test_api_client.py:579` — `JobHunter`
- `apps/bot/tests/test_api_client.py:615` — `JobHunter`
- `apps/bot/tests/test_api_client.py:634` — `JobHunter`
- `apps/bot/tests/test_cover_letter.py:18` — `JobHunter`
- `apps/bot/tests/test_cover_letter.py:328` — `JobHunter`
- `apps/bot/tests/test_reminder_compatibility.py:5` — `JobHunter`
- `apps/bot/tests/test_reminder_compatibility.py:15` — `JobHunter`
- `apps/bot/tests/test_service_auth.py:8` — `JobHunter`
- `apps/bot/tests/test_service_auth.py:20` — `JobHunter`
- `apps/bot/tests/test_service_auth.py:48` — `JobHunter`
- `apps/bot/tests/test_service_auth.py:63` — `JobHunter`
- `apps/bot/tests/test_service_auth.py:70` — `JobHunter`
- `apps/bot/tests/test_service_auth.py:75` — `JobHunter`
- `apps/bot/tests/test_telegram_auth.py:15` — `JobHunter`
- `apps/bot/tests/test_telegram_auth.py:112` — `JobHunter`
- `apps/web/lib/server/cookie.ts:10` — `job_hunter`, `job_hunter`
- `apps/web/lib/server/telegram-auth.ts:16` — `job_hunter`
- `apps/web/lib/server/telegram-auth.ts:17` — `job_hunter`
- `apps/web/tests/auth-request-isolation.test.ts:46` — `job_hunter`
- `apps/web/tests/auth-server.test.ts:67` — `job_hunter`
- `apps/web/tests/auth-server.test.ts:114` — `job_hunter`, `job_hunter`
- `apps/web/tests/auth-server.test.ts:247` — `job_hunter`
- `apps/web/tests/auth-server.test.ts:249` — `job_hunter`
- `apps/web/tests/auth-server.test.ts:250` — `job_hunter`
- `apps/web/tests/email-auth-server.test.ts:74` — `job_hunter`
- `apps/web/tests/session-cutover.test.ts:32` — `job_hunter`
- `apps/web/tests/telegram-auth-binding-race.test.ts:58` — `job_hunter`
- `apps/web/tests/telegram-auth-binding-race.test.ts:149` — `job_hunter`
- `apps/web/tests/telegram-auth-binding-race.test.ts:209` — `job_hunter`
- `apps/web/tests/telegram-auth-binding-race.test.ts:215` — `job_hunter`
- `apps/web/tests/telegram-auth-binding-race.test.ts:223` — `job_hunter`
- `apps/web/tests/telegram-auth-binding-race.test.ts:270` — `job_hunter`
- `apps/web/tests/telegram-auth-binding-race.test.ts:306` — `job_hunter`
- `apps/web/tests/telegram-auth-server.test.ts:48` — `job_hunter`
- `apps/web/tests/telegram-auth-server.test.ts:84` — `job_hunter`
- `apps/web/tests/telegram-auth-server.test.ts:89` — `job_hunter`
- `apps/web/tests/telegram-auth-server.test.ts:96` — `job_hunter`
- `apps/web/tests/telegram-auth-server.test.ts:126` — `job_hunter`
- `apps/web/tests/telegram-auth-server.test.ts:127` — `job_hunter`
- `apps/web/tests/telegram-auth-server.test.ts:174` — `job_hunter`
- `apps/web/tests/telegram-auth-server.test.ts:204` — `job_hunter`
- `apps/web/tests/telegram-auth-server.test.ts:211` — `job_hunter`
- `apps/web/tests/telegram-auth-server.test.ts:214` — `job_hunter`
- `apps/web/tests/telegram-auth-server.test.ts:288` — `job_hunter`
- `apps/web/tests/telegram-auth-server.test.ts:292` — `job_hunter`
- `docker-compose.yml:5` — `job_hunter`
- `docker-compose.yml:6` — `job_hunter`
- `docker-compose.yml:7` — `job_hunter`
- `docker-compose.yml:9` — `job_hunter`, `job_hunter`
- `docs/auth-slice3.md:14` — `job_hunter`
- `docs/auth-slice3.md:17` — `job_hunter`
- `docs/auth-slice4.md:20` — `job_hunter`
- `docs/auth-slice4.md:21` — `job_hunter`
- `docs/auth-slice6.md:131` — `job-hunter`
- `scripts/dev.sh:48` — `job_hunter`, `job_hunter`
- `scripts/dev.sh:88` — `job-hunter`
