import type { ApplicationStatusHistory } from "../lib/contracts";
import { statusLabels } from "./vacancy";

const dateTimeFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export function ApplicationHistory({
  open,
  loading,
  history,
  error,
  onToggle,
  onRetry,
}: {
  open: boolean;
  loading: boolean;
  history: ApplicationStatusHistory | null;
  error: boolean;
  onToggle: () => void;
  onRetry: () => void;
}) {
  return (
    <section className="application-history" aria-labelledby="history-heading">
      <div className="application-history-heading">
        <h2 id="history-heading">История статусов</h2>
        <button
          type="button"
          aria-label={
            open ? "Скрыть историю статусов" : "Показать историю статусов"
          }
          aria-expanded={open}
          aria-controls="application-history-content"
          data-expanded={open}
          onClick={onToggle}
        >
          {open ? "Скрыть" : "Показать"}
        </button>
      </div>
      <div id="application-history-content" hidden={!open} aria-busy={loading}>
        {loading && (
          <p className="application-history-message" role="status">
            {history
              ? "Обновляем историю статусов…"
              : "Загружаем историю статусов…"}
          </p>
        )}
        {error && (
          <div className="application-history-error" role="alert">
            <p>Не удалось загрузить историю статусов.</p>
            <button type="button" onClick={onRetry} disabled={loading}>
              Повторить
            </button>
          </div>
        )}
        {history?.items.length === 0 && (
          <p className="application-history-message">
            История изменений пока отсутствует.
          </p>
        )}
        {history && history.items.length > 0 && (
          <ol className="application-history-list">
            {history.items.map((item, index) => (
              <li key={index}>
                <strong>{statusLabels[item.status]}</strong>
                <time dateTime={item.occurred_at}>
                  {dateTimeFormatter.format(new Date(item.occurred_at))}
                </time>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
