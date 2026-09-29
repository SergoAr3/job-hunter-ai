"use client";
import { useRef, useState } from "react";
import Link from "next/link";
import type { DiscoverPage, SaveResult, Vacancy } from "../lib/contracts";
import { webRequest } from "../lib/client";
import { errorMessage } from "../lib/errors";
import {
  JobMetadata,
  JobText,
  Matching,
  Salary,
  SourceLink,
  statusLabels,
  workplace,
} from "./vacancy";
import { compactLocation } from "../lib/presentation";
const keyOf = (item: Vacancy) =>
  JSON.stringify([item.source, item.source_scope, item.external_id]);
function cardAccessibleName(item: Vacancy, items: Vacancy[], index: number) {
  const location = compactLocation(item.location);
  const name = [item.title, item.company, location].filter(Boolean).join(" — ");
  const duplicated = items.some(
    (other, otherIndex) =>
      otherIndex !== index &&
      other.title === item.title &&
      other.company === item.company &&
      compactLocation(other.location) === location,
  );
  return duplicated ? `${name} — результат ${index + 1}` : name;
}
export function Discover() {
  const [query, setQuery] = useState("");
  const [remote, setRemote] = useState(false);
  const [committed, setCommitted] = useState({ query: "", remote: false });
  const [page, setPage] = useState<DiscoverPage | null>(null);
  const [selected, setSelected] = useState<Vacancy | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [saveErrors, setSaveErrors] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<Record<string, SaveResult>>({});
  const [pending, setPending] = useState<string | null>(null);
  const generation = useRef(0);
  const saveLock = useRef(false);
  const detailPanel = useRef<HTMLElement | null>(null);
  const resultsPanel = useRef<HTMLDivElement | null>(null);
  const selectedButton = useRef<HTMLButtonElement | null>(null);
  const searchAbort = useRef<AbortController | null>(null);
  const lastSearch = useRef({ query: "", remote: false, offset: 0 });
  async function search(text: string, onlyRemote: boolean, offset = 0) {
    const current = ++generation.current;
    searchAbort.current?.abort();
    const controller = new AbortController();
    searchAbort.current = controller;
    lastSearch.current = { query: text, remote: onlyRemote, offset };
    setCommitted({ query: text, remote: onlyRemote });
    setLoading(true);
    setError("");
    setSelected(null);
    setPage(null);
    try {
      const params = new URLSearchParams({
        query: text,
        remote_only: String(onlyRemote),
        offset: String(offset),
      });
      const result = await webRequest<DiscoverPage>(`/api/discover?${params}`, {
        signal: controller.signal,
      });
      if (generation.current !== current) return;
      setPage(result);
    } catch (error) {
      if (generation.current === current) setError(errorMessage(error));
    } finally {
      if (generation.current === current) setLoading(false);
    }
  }
  async function save(item: Vacancy) {
    const key = keyOf(item);
    if (saveLock.current || item.already_saved_for_user || saved[key]) return;
    saveLock.current = true;
    setPending(key);
    setSaveErrors((previous) => ({ ...previous, [key]: "" }));
    try {
      const { source, source_scope, external_id } = item;
      const result = await webRequest<SaveResult>("/api/discover/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source, source_scope, external_id }),
      });
      setSaved((previous) => ({ ...previous, [key]: result }));
    } catch (error) {
      setSaveErrors((previous) => ({
        ...previous,
        [key]: errorMessage(error),
      }));
    } finally {
      saveLock.current = false;
      setPending(null);
    }
  }
  const selectionKey = selected ? keyOf(selected) : "";
  const result = saved[selectionKey];
  return (
    <>
      <header className="page-heading">
        <span className="eyebrow">ПОИСК ВОЗМОЖНОСТЕЙ</span>
        <h1>Поиск вакансий</h1>
        <p>Найдите подходящую роль. Сохраните, чтобы вернуться к ней позже.</p>
      </header>
      <form
        className="search-panel"
        onSubmit={(event) => {
          event.preventDefault();
          if (query.trim()) void search(query.trim(), remote);
        }}
      >
        <label htmlFor="query">Должность или ключевые слова</label>
        <div className="search-row">
          <input
            id="query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Например, Python разработчик"
            maxLength={200}
            required
          />
          <button
            className="primary"
            type="submit"
            disabled={
              !query.trim() ||
              (loading &&
                query.trim() === committed.query &&
                remote === committed.remote)
            }
            aria-label="Найти вакансии"
            aria-busy={loading}
          >
            {loading &&
            query.trim() === committed.query &&
            remote === committed.remote
              ? "Ищем…"
              : "Найти вакансии"}
          </button>
        </div>
        <div className="search-options">
          <label className="checkbox">
            <input
              type="checkbox"
              checked={remote}
              onChange={(event) => setRemote(event.target.checked)}
            />
            Только удалённая работа
          </label>
          <span className="source-label">
            Работа России <span aria-hidden="true">·</span> RU
          </span>
        </div>
      </form>
      <div aria-live="polite">
        {loading && (
          <div className="state loading-state" role="status">
            <span className="loading-dot" aria-hidden="true" />
            Ищем вакансии у источника…
          </div>
        )}
        {error && (
          <div className="notice error" role="alert">
            <p>{error}</p>
            <button
              onClick={() =>
                void search(
                  lastSearch.current.query,
                  lastSearch.current.remote,
                  lastSearch.current.offset,
                )
              }
            >
              Повторить поиск
            </button>
          </div>
        )}
      </div>
      {!page && !loading && !error && (
        <div className="state">
          <span className="state-symbol" aria-hidden="true">
            ⌕
          </span>
          <h2>Начните с того, что вам интересно</h2>
          <p>
            Введите название роли или навык. Здесь появятся актуальные вакансии
            источника.
          </p>
        </div>
      )}
      {page && (
        <>
          <div className="results-heading">
            <h2>Результаты поиска</h2>
            <span>
              {page.returned_count} на странице
              {page.locally_filtered ? " · фильтр удалённой работы" : ""}
            </span>
          </div>
          <div className={`discover-grid ${selected ? "has-selection" : ""}`}>
            <div className="results-list" ref={resultsPanel}>
              {page.items.length === 0 && (
                <div className="state">
                  <h3>На этой странице ничего не найдено</h3>
                  <p>
                    {page.next_offset !== null
                      ? "У источника есть следующая страница. Продолжите поиск."
                      : "Попробуйте другой запрос."}
                  </p>
                </div>
              )}
              {page.items.map((item, index) => (
                <button
                  className={`vacancy-card ${selected && keyOf(selected) === keyOf(item) ? "selected" : ""}`}
                  key={keyOf(item)}
                  aria-label={cardAccessibleName(item, page.items, index)}
                  onClick={(event) => {
                    selectedButton.current = event.currentTarget;
                    setSelected(item);
                    if (window.innerWidth <= 900) {
                      requestAnimationFrame(() => {
                        detailPanel.current?.focus();
                        detailPanel.current?.scrollIntoView({ block: "start" });
                      });
                    }
                  }}
                  aria-pressed={
                    selected ? keyOf(selected) === keyOf(item) : false
                  }
                >
                  <span className="card-main">
                    <span className="card-heading">
                      <span className="card-title">{item.title}</span>
                      {selected && keyOf(selected) === keyOf(item) && (
                        <span className="selection-mark" aria-label="Выбрана">
                          ✓
                        </span>
                      )}
                    </span>
                    <span className="company">{item.company}</span>
                    {!selected && item.description?.trim() ? (
                      <span className="card-description">
                        {item.description}
                      </span>
                    ) : null}
                  </span>
                  <span className="card-aside">
                    <span className="salary">
                      <Salary job={item} />
                    </span>
                    <span className="card-metadata">
                      {item.location && (
                        <span title={item.location}>
                          {compactLocation(item.location)}
                        </span>
                      )}
                      {workplace[item.workplace_type] && (
                        <span className="workplace-pill">
                          {workplace[item.workplace_type]}
                        </span>
                      )}
                    </span>
                    {(item.already_saved_for_user || saved[keyOf(item)]) && (
                      <span className="badge saved-badge">
                        В моих вакансиях
                      </span>
                    )}
                  </span>
                </button>
              ))}
              <nav className="pagination" aria-label="Страницы результатов">
                <button
                  disabled={page.offset === 0}
                  onClick={() =>
                    void search(
                      committed.query,
                      committed.remote,
                      Math.max(0, page.offset - page.limit),
                    )
                  }
                >
                  ← Назад
                </button>
                <span>Страница {Math.floor(page.offset / page.limit) + 1}</span>
                <button
                  disabled={page.next_offset === null}
                  onClick={() =>
                    page.next_offset !== null &&
                    void search(
                      committed.query,
                      committed.remote,
                      page.next_offset,
                    )
                  }
                >
                  Далее →
                </button>
              </nav>
            </div>
            {selected && (
              <article
                ref={detailPanel}
                className="detail-panel"
                aria-label="Описание вакансии"
                tabIndex={-1}
              >
                <>
                  <button
                    className="mobile-back"
                    onClick={() => {
                      setSelected(null);
                      requestAnimationFrame(() => {
                        selectedButton.current?.focus();
                        selectedButton.current?.scrollIntoView({
                          block: "nearest",
                        });
                      });
                    }}
                  >
                    ← К результатам
                  </button>
                  <header className="vacancy-header">
                    <span className="eyebrow">ВАКАНСИЯ · РАБОТА РОССИИ</span>
                    <h2>{selected.title}</h2>
                    <p className="company">{selected.company}</p>
                    <JobMetadata job={selected} />
                  </header>
                  <div className="save-area">
                    {result ? (
                      <div role="status">
                        <p className="badge saved-badge">
                          {result.application_created
                            ? "Вакансия сохранена"
                            : "Вакансия уже была в ваших откликах"}
                        </p>
                        <p>
                          Текущий статус:{" "}
                          {statusLabels[result.application.status] ??
                            result.application.status}
                        </p>
                        <Link
                          className="primary button-link"
                          href={`/applications/${result.application.id}`}
                        >
                          Открыть сохранённую вакансию →
                        </Link>
                      </div>
                    ) : selected.already_saved_for_user ? (
                      <p className="badge saved-badge">Уже в моих вакансиях</p>
                    ) : (
                      <button
                        className="primary"
                        disabled={pending !== null}
                        onClick={() => void save(selected)}
                      >
                        {pending === selectionKey
                          ? "Сохраняем…"
                          : "Сохранить вакансию"}
                      </button>
                    )}
                    {saveErrors[selectionKey] && (
                      <p className="notice error" role="alert">
                        {saveErrors[selectionKey]}
                      </p>
                    )}
                    <SourceLink url={selected.source_url} />
                  </div>
                  <JobText job={selected} />
                  <Matching preview={selected.preview_match} />
                </>
              </article>
            )}
          </div>
        </>
      )}
    </>
  );
}
