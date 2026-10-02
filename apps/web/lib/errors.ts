export class WebError extends Error {
  constructor(
    public code: string,
    public status = 502,
    public fieldErrors?: Record<string, string>,
  ) {
    super(code);
  }
}
const messages: Record<string, string> = {
  ACCOUNT_LINK_CONFLICT: "Этот Telegram уже связан с другим аккаунтом.",
  TELEGRAM_CHALLENGE_INVALID: "Запрос Telegram недействителен. Начните заново.",
  TELEGRAM_CHALLENGE_EXPIRED: "Время подтверждения истекло. Начните заново.",
  TELEGRAM_CHALLENGE_CANCELLED: "Подтверждение отменено в Telegram.",
  TELEGRAM_CHALLENGE_CONSUMED: "Запрос уже завершён. Начните заново.",
  TELEGRAM_CHALLENGE_CONFLICT: "Этот Telegram уже связан с другим аккаунтом.",
  TELEGRAM_CHALLENGE_PENDING: "Подтвердите запрос в Telegram.",
  AUTH_INVALID_CREDENTIALS: "Не удалось подтвердить пароль аккаунта.",
  unauthenticated: "Сессия завершилась. Войдите снова.",
  auth_invalid_credentials: "Не удалось войти. Проверьте email и пароль.",
  auth_invalid: "Проверьте введённые данные.",
  auth_unavailable: "Вход временно недоступен. Повторите попытку позже.",
  configuration:
    "Web не настроен. Проверьте серверный API_BASE_URL и перезапустите Web.",
  api_unavailable:
    "API временно недоступен. Проверьте его запуск и повторите запрос.",
  source_timeout:
    "Источник вакансий отвечает слишком долго. Повторите поиск позже.",
  source_unavailable: "Источник вакансий временно недоступен.",
  source_rate_limited: "Источник ограничил частоту запросов. Попробуйте позже.",
  source_bad_response: "Не удалось прочитать ответ источника. Повторите поиск.",
  vacancy_not_found: "Вакансия больше недоступна у источника. Обновите поиск.",
  source_identity_conflict:
    "Не удалось безопасно сохранить вакансию. Обновите поиск.",
  USER_NOT_FOUND: "Аккаунт недоступен. Войдите снова.",
  APPLICATION_NOT_FOUND: "Вакансия не найдена.",
  ambiguous_save:
    "Не удалось подтвердить результат сохранения. Запись могла быть создана. Можно повторить Save: API переиспользует существующую запись.",
  invalid_request: "Проверьте введённые данные и повторите запрос.",
  save_failed: "Не удалось сохранить вакансию. Повторите попытку.",
  ambiguous_status:
    "Не удалось подтвердить изменение статуса. Проверьте актуальное состояние.",
  status_invalid: "Не удалось изменить статус. Выберите допустимое значение.",
  status_failed: "Не удалось изменить статус. Повторите попытку.",
  status_unconfirmed:
    "Изменение отправлено, но актуальный статус не удалось подтвердить. Обновите данные.",
  profile_missing: "Профиль ещё не создан.",
  profile_user_not_found: "Аккаунт недоступен. Войдите снова.",
  profile_invalid: "Проверьте поля профиля и повторите сохранение.",
  ambiguous_profile:
    "Не удалось подтвердить результат сохранения. Изменения могли сохраниться. Обновите данные.",
  profile_unconfirmed:
    "Изменение отправлено, но актуальный профиль не удалось загрузить. Обновите данные.",
};
export function errorMessage(error: unknown): string {
  return error instanceof WebError
    ? (messages[error.code] ?? "Не удалось получить данные. Повторите запрос.")
    : "Не удалось получить данные. Повторите запрос.";
}
