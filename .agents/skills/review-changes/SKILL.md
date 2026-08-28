---
name: review-changes
description: Строго и read-only проверять текущий diff перед commit или merge, находя воспроизводимые риски, а не пересказывая изменения.
---

# Review changes

Используй этот skill для code review текущих изменений перед commit или merge.
Review и объяснения пиши на русском; technical terms, code identifiers и имена
файлов можно оставлять на English.

## Boundaries

- Сначала прочитай корневой `AGENTS.md` и соблюдай его приоритет в случае
  конфликта.
- Это строго read-only workflow: не изменяй код или тесты и не создавай fixes.
- Не выполняй `commit`, `push`, создание PR, `merge`, `stash`, `reset`,
  `checkout`, `restore` или другие изменяющие Git-операции.
- Любые fixes допустимы только после отдельного явного запроса пользователя.
- Не создавай findings ради stylistic preferences и не завышай severity.

## Review workflow

1. Определи текущую ветку и проверь `git status`.
2. Выбери подходящую base branch — обычно `main` — и изучи diff относительно
   неё. Если base неочевидна, явно укажи допущение или запроси направление.
3. Открой code вокруг изменённых участков, relevant call sites, models,
   migrations и tests. Не рассматривай diff изолированно.
4. Определи риск изменений и углуби review только в релевантных областях.
5. Сверь implementation с intended behavior и проверь adequacy tests: passing
   tests сами по себе не доказывают корректность.
6. При необходимости запускай только checks, которые можно безопасно выполнить
   без изменения состояния repository.
7. Перед verdict проверь, что каждый finding относится к текущему diff или был
   существенно затронут им. Проблему, существовавшую до изменений и не
   созданную или не ухудшенную ими, явно пометь как `pre-existing`; не делай её
   blocker текущего commit/merge без отдельной причины. Review не является
   аудитом всего repository.
8. Если finding основан на предположении, а не подтверждённом execution path
   или behavior, явно пометь его как `hypothesis` и укажи, что проверить для
   подтверждения. Не ставь P0/P1 без достаточного подтверждения, особенно для
   concurrency, security и framework behavior.
9. Сформируй конкретные, воспроизводимые findings. После этого остановись: не
   исправляй найденные проблемы.

## Review areas

Проверяй только области, которых касается diff.

- **Correctness / business logic:** requirements, edge cases, nullable и empty
  values, state transitions, unexpected branches, idempotency.
- **Database / SQLAlchemy:** model и migration consistency; nullable, defaults,
  constraints и foreign keys; transaction boundaries, commit/rollback,
  savepoints и session после exceptions; concurrency, legacy data/backfill,
  upgrade/downgrade и PostgreSQL vs SQLite. Предварительный `SELECT` не даёт
  concurrency guarantee, если hard guarantee должна обеспечиваться DB constraint.
- **External network / security:** для user-controlled URL и outbound requests
  проверяй SSRF (scheme, hostname, ports, private/loopback/link-local/reserved
  IP, IPv4/IPv6, DNS rebinding/TOCTOU, redirects), timeouts и total deadline,
  response size/content type, TLS, retries, cleanup и error mapping. Security
  test не доказывает защиту, если fake сам бросает ожидаемую ошибку, минуя
  production validation.
- **Resource ownership:** ownership sockets/files/clients/sessions и cleanup на
  success и exception, включая leaks и double-close.
- **Parsers / external data:** malformed variants, types/shapes, limits,
  invalid или inverted numeric ranges, partial data, 500 от untrusted input и
  защита DB constraints.
- **API:** request validation, response schema, status codes, backward
  compatibility, nullable fields/enums и раскрытие internal errors.
- **BOT/client:** thin-client boundary, error UX, nullable values, API failures,
  message limits, state transitions, retry/cancel behavior.
- **Tests:** regression, negative, race/security и migration-specific paths;
  отсутствие real network; tests должны исполнять production logic, а не быть
  tautological.
- **Scope:** несогласованные features, premature infrastructure и broad
  refactors без необходимости.

## Severity and findings

- **P0 — critical:** нельзя commit/merge: потеря данных, critical security
  vulnerability или fundamentally broken behavior.
- **P1 — must fix:** исправить до commit/merge.
- **P2 — should fix:** желательно исправить или оформить отдельной задачей;
  не blocker текущего change.
- **P3 — minor:** minor maintainability или style concern.

Каждый finding должен содержать severity, файл, function/class/участок,
конкретный failure scenario, почему это проблема, минимальный рекомендуемый fix
и необходимость regression test. Для нужного test укажи scenario. Предпочитай
минимальный fix широкому refactor. Для каждого P0/P1 рекомендуй regression test.

Используй такой формат:

```markdown
### P1 — краткое название

File: `path/to/file.py`
Area: `function_or_class`

Scenario:
...

Why:
...

Minimal fix:
...

Regression test:
...
```

## Output

```markdown
# Verdict

Ready for commit/merge | Needs fixes | Major rework required

Краткое объяснение.

# Findings

## P1
...

# Focused review

Только релевантные sections, например Transaction review, Security / SSRF
review, Migration review, Parsing robustness review, API review или BOT review.

# Must fix before commit/merge

Конкретный список P0/P1 или `Нет.`

# Can defer

Релевантные P2/P3 или `Нет.`

# Tests missing

Только реально недостающие regression/security/integration tests или `Нет.`

# Scope check

Подтверди, есть ли scope creep.
```

Не выводи пустые severity categories. Если P0/P1 нет, явно сообщи, что changes
готовы к следующему этапу.
