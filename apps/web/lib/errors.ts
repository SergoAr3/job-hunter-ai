export class WebError extends Error {
  constructor(
    public code: string,
    public status = 502,
  ) {
    super(code);
  }
}
const messages: Record<string, string> = {
  configuration:
    "Web не настроен. Для make dev укажите корректный WEB_DEV_USER_ID в корневом .env. При отдельном запуске Web настройте WEB_DEV_USER_ID и API_BASE_URL в apps/web/.env.local. Перезапустите Web. Это локальный режим, не авторизация.",
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
  USER_NOT_FOUND:
    "Настроенный пользователь не найден. Проверьте WEB_DEV_USER_ID: нужен внутренний ID существующего пользователя.",
  APPLICATION_NOT_FOUND: "Вакансия не найдена у настроенного пользователя.",
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
};
export function errorMessage(error: unknown): string {
  return error instanceof WebError
    ? (messages[error.code] ?? "Не удалось получить данные. Повторите запрос.")
    : "Не удалось получить данные. Повторите запрос.";
}
