"use client";

type LoadingMode = "initial" | "next" | "previous" | "refresh" | "replay";

function loadingCopy(mode: LoadingMode, slow: boolean) {
  if (mode === "replay")
    return {
      title: "Загружаем результаты…",
      description: "Обновляем данные сохранённых результатов",
    };
  const title = slow
    ? "Источник отвечает медленнее обычного"
    : mode === "next"
      ? "Загружаем следующую страницу…"
      : mode === "previous"
        ? "Загружаем предыдущую страницу…"
        : mode === "refresh"
          ? "Обновляем результаты…"
          : "Ищем подходящие вакансии…";
  const description = !slow
    ? "Проверяем источник и обновляем результаты"
    : mode === "next"
      ? "Продолжаем загружать следующую страницу"
      : mode === "previous"
        ? "Продолжаем загружать предыдущую страницу"
        : "Продолжаем ждать актуальные вакансии";
  return { title, description };
}

// Both presentations consume the same request-scoped slow state in Discover.
export function DiscoverPaginationLoading({
  mode,
  slow,
}: {
  mode: LoadingMode;
  slow: boolean;
}) {
  const { title, description } = loadingCopy(mode, slow);
  return (
    <span className="discover-pagination-feedback" aria-hidden="true">
      <span className="discover-pagination-title">{title}</span>
      {slow && <span>{description}</span>}
    </span>
  );
}

export function DiscoverLoading({
  mode,
  id,
  slow,
}: {
  mode: LoadingMode;
  id: string;
  slow: boolean;
}) {
  const { title, description } = loadingCopy(mode, slow);
  return (
    <>
      <div
        className="discover-loading"
        id={id}
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        <span className="discover-search-motion" aria-hidden="true">
          <svg viewBox="0 0 32 32" fill="none">
            <circle cx="13" cy="13" r="7" />
            <path d="m18.5 18.5 6 6" />
          </svg>
          <span className="discover-search-points">
            <i />
            <i />
            <i />
          </span>
        </span>
        <div className="discover-loading-copy">
          <p className="discover-loading-title">{title}</p>
          <p className="discover-loading-description">{description}</p>
          {mode !== "initial" && (
            <p className="discover-loading-context">
              Пока показаны предыдущие результаты. Действия временно недоступны.
            </p>
          )}
        </div>
      </div>
      {mode === "initial" && (
        <div className="discover-skeletons" aria-hidden="true">
          {Array.from({ length: 4 }, (_, index) => (
            <div className="discover-skeleton-card" key={index}>
              <div className="discover-skeleton-main">
                <span className="discover-skeleton-line skeleton-title" />
                <span className="discover-skeleton-line skeleton-company" />
                <div className="discover-skeleton-meta">
                  <span className="discover-skeleton-line" />
                  <span className="discover-skeleton-line" />
                </div>
                <span className="discover-skeleton-line" />
                <span className="discover-skeleton-line skeleton-description" />
              </div>
              <span className="discover-skeleton-line skeleton-action" />
            </div>
          ))}
        </div>
      )}
    </>
  );
}
