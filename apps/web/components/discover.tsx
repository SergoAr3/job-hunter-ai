"use client";
import { useEffect, useId, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  discoverUrl,
  parseDiscoverState,
  vacancyKey as keyOf,
  type DiscoverState,
} from "../lib/discover";
import { DiscoverRoleSuggestions } from "./discover-role-suggestions";
import { DiscoverLoading, DiscoverPaginationLoading } from "./discover-loading";
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
function reconcileSave(item: Vacancy, result?: SaveResult): Vacancy {
  // A loaded search Application wins over an older mutation response. Only
  // fill missing knowledge from a successful Save of this exact identity.
  if (!result || item.application_id != null) return item;
  return {
    ...item,
    already_saved_for_user: true,
    application_id: result.application.id,
    application_status: result.application
      .status as Vacancy["application_status"],
  };
}
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
  const router = useRouter();
  const raw = useSearchParams().toString();
  const state = parseDiscoverState(new URLSearchParams(raw));
  const currentUrl = discoverUrl(state);
  const searchUrl = discoverUrl({ ...state, selected: null });
  const [draft, setDraft] = useState({
    searchUrl,
    query: state.query,
    remote: state.remote,
    revision: 0,
  });
  // Reset controls before rendering a new search. The revision also makes a
  // return to A distinct from an earlier A result, without storing past pages.
  if (draft.searchUrl !== searchUrl)
    setDraft({
      searchUrl,
      query: state.query,
      remote: state.remote,
      revision: draft.revision + 1,
    });
  const { query, remote } = draft;
  const [attempt, setAttempt] = useState(0);
  const requestKey = `${searchUrl}:${draft.revision}:${attempt}`;
  const [result, setResult] = useState<{
    key: string;
    searchUrl: string;
    page: DiscoverPage | null;
    error: string;
  } | null>(null);
  const currentPage = result?.key === requestKey ? result.page : null;
  const error = result?.key === requestKey ? result.error : "";
  const loading = !!state.query && result?.key !== requestKey;
  const [slowRequestKey, setSlowRequestKey] = useState<string | null>(null);
  useEffect(() => {
    if (!loading) return;
    let active = true;
    const timer = window.setTimeout(() => {
      if (active) setSlowRequestKey(requestKey);
    }, 2800);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [loading, requestKey]);
  const slow = loading && slowRequestKey === requestKey;
  // Retain only the last displayed page as a disabled loading placeholder.
  // It never supplies current selection, actions or authoritative URL state.
  const page = currentPage ?? (loading ? result?.page : null);
  const stale = loading && !!page;
  const previousState = result
    ? parseDiscoverState(new URLSearchParams(result.searchUrl.split("?")[1]))
    : null;
  const sameSearch =
    previousState?.query === state.query &&
    previousState?.remote === state.remote;
  const loadingMode = !page
    ? "initial"
    : sameSearch && state.offset > page.offset
      ? "next"
      : sameSearch && state.offset < page.offset
        ? "previous"
        : "refresh";
  const loadingId = useId();
  const selected =
    currentPage?.items.find((item) => keyOf(item) === state.selected) ?? null;
  const [saveErrors, setSaveErrors] = useState<Record<string, string>>({});
  const [saveFeedback, setSaveFeedback] = useState<Record<string, boolean>>({});
  // Outcomes only bridge a search that overlapped Save. Rendered Application
  // state lives in page.items; this buffer resets when a new search starts.
  const completedSaves = useRef<Record<string, SaveResult>>({});
  const [pending, setPending] = useState<string | null>(null);
  const generation = useRef(0);
  const saveLock = useRef(false);
  const detailPanel = useRef<HTMLElement | null>(null);
  const resultsPanel = useRef<HTMLDivElement | null>(null);
  const request = useRef<{
    key: string;
    controller: AbortController;
    promise: Promise<DiscoverPage>;
    subscribers: number;
  } | null>(null);
  useEffect(() => {
    if (raw !== (currentUrl.split("?")[1] ?? ""))
      router.replace(currentUrl, { scroll: false });
  }, [raw, currentUrl, router]);

  const { query: confirmedQuery, remote: confirmedRemote, offset } = state;
  useEffect(() => {
    const current = ++generation.current;
    let active = true;
    completedSaves.current = {};
    if (!confirmedQuery) {
      request.current?.controller.abort();
      request.current = null;
      return;
    }
    if (request.current?.key !== requestKey) {
      request.current?.controller.abort();
      const controller = new AbortController();
      const params = new URLSearchParams({
        query: confirmedQuery,
        remote_only: String(confirmedRemote),
        offset: String(offset),
      });
      request.current = {
        key: requestKey,
        controller,
        subscribers: 0,
        promise: webRequest<DiscoverPage>(`/api/discover?${params}`, {
          signal: controller.signal,
        }),
      };
    }
    const pendingRequest = request.current;
    pendingRequest.subscribers++;
    void pendingRequest.promise
      .then((fresh) => {
        if (!active || generation.current !== current) return;
        setResult({
          key: requestKey,
          searchUrl,
          error: "",
          page: {
            ...fresh,
            items: fresh.items.map((item) =>
              reconcileSave(item, completedSaves.current[keyOf(item)]),
            ),
          },
        });
        completedSaves.current = {};
        setSaveFeedback({});
        setSaveErrors({});
      })
      .catch((failure) => {
        if (active && generation.current === current)
          setResult({
            key: requestKey,
            searchUrl,
            page: null,
            error: errorMessage(failure),
          });
      })
      .finally(() => {
        if (request.current === pendingRequest) request.current = null;
      });
    return () => {
      active = false;
      pendingRequest.subscribers--;
      // Effect replay reuses the pending request. Real unmount aborts it.
      queueMicrotask(() => {
        if (pendingRequest.subscribers === 0) pendingRequest.controller.abort();
      });
    };
  }, [confirmedQuery, confirmedRemote, offset, requestKey, searchUrl]);

  useEffect(() => {
    function refresh(event: PageTransitionEvent) {
      if (event.persisted) setAttempt((value) => value + 1);
    }
    window.addEventListener("pageshow", refresh);
    return () => window.removeEventListener("pageshow", refresh);
  }, []);

  const visibleSelection = selected ? keyOf(selected) : null;
  useEffect(() => {
    if (visibleSelection && window.innerWidth <= 900) {
      detailPanel.current?.focus();
      detailPanel.current?.scrollIntoView({ block: "start" });
    }
  }, [visibleSelection]);

  function navigate(next: DiscoverState) {
    const url = discoverUrl(next);
    if (discoverUrl({ ...next, selected: null }) === searchUrl)
      setAttempt((value) => value + 1);
    if (url !== currentUrl) router.push(url, { scroll: false });
  }
  function selectIdentity(selected: string | null) {
    // Next integrates native history with useSearchParams. Selection needs no
    // server navigation/auth request, and replaces rather than adds an entry.
    window.history.replaceState(null, "", discoverUrl({ ...state, selected }));
  }
  async function save(item: Vacancy) {
    const key = keyOf(item);
    if (saveLock.current || item.already_saved_for_user) return;
    saveLock.current = true;
    const current = generation.current;
    setPending(key);
    setSaveErrors((previous) => ({ ...previous, [key]: "" }));
    try {
      const { source, source_scope, external_id } = item;
      const result = await webRequest<SaveResult>("/api/discover/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source, source_scope, external_id }),
      });
      completedSaves.current[key] = result;
      setResult(
        (previous) =>
          previous && {
            ...previous,
            page: previous.page && {
              ...previous.page,
              items: previous.page.items.map((candidate) =>
                keyOf(candidate) === key
                  ? reconcileSave(candidate, result)
                  : candidate,
              ),
            },
          },
      );
      setSaveFeedback((previous) => ({
        ...previous,
        [key]: result.application_created,
      }));
    } catch (error) {
      if (generation.current === current)
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
  const savedNow = saveFeedback[selectionKey];
  const applicationId = selected?.application_id;
  const applicationStatus = selected?.application_status;
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
          if (query.trim())
            navigate({
              query: query.trim(),
              remote,
              offset: 0,
              selected: null,
            });
        }}
      >
        <label htmlFor="query">Должность или ключевые слова</label>
        <div className="search-row">
          <input
            id="query"
            value={query}
            onChange={(event) =>
              setDraft({ ...draft, query: event.target.value })
            }
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
                query.trim() === confirmedQuery &&
                remote === confirmedRemote)
            }
            aria-label="Найти вакансии"
            aria-busy={loading}
          >
            {loading &&
            query.trim() === confirmedQuery &&
            remote === confirmedRemote
              ? "Ищем…"
              : "Найти вакансии"}
          </button>
        </div>
        <div className="search-options">
          <label className="checkbox">
            <input
              type="checkbox"
              checked={remote}
              onChange={(event) =>
                setDraft({ ...draft, remote: event.target.checked })
              }
            />
            Только удалённая работа
          </label>
          <span className="source-label">
            Работа России <span aria-hidden="true">·</span> RU
          </span>
        </div>
        <DiscoverRoleSuggestions
          onSelect={(role) => setDraft({ ...draft, query: role })}
        />
      </form>
      {loading && (
        <DiscoverLoading mode={loadingMode} id={loadingId} slow={slow} />
      )}
      <div aria-live="polite">
        {error && (
          <div className="notice error" role="alert">
            <p>{error}</p>
            <button onClick={() => setAttempt((value) => value + 1)}>
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
        <section
          className={`discover-results${stale ? " is-stale" : ""}`}
          aria-label="Результаты поиска"
          aria-busy={stale}
          aria-describedby={stale ? loadingId : undefined}
        >
          <div className="results-heading">
            <h2>Результаты поиска</h2>
            {stale && (
              <span className="discover-stale-label">
                Предыдущие результаты · страница{" "}
                {Math.floor(page.offset / page.limit) + 1}
              </span>
            )}
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
              {page.items.map((item, index) => {
                const status = item.application_status;
                return (
                  <button
                    className={`vacancy-card ${selected && keyOf(selected) === keyOf(item) ? "selected" : ""}`}
                    key={keyOf(item)}
                    disabled={stale}
                    aria-label={cardAccessibleName(item, page.items, index)}
                    onClick={() => selectIdentity(keyOf(item))}
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
                      {status && (
                        <span className="badge saved-badge">
                          {statusLabels[status] ?? status}
                        </span>
                      )}
                    </span>
                  </button>
                );
              })}
              <nav
                className="pagination discover-pagination"
                aria-label="Страницы результатов"
              >
                <button
                  disabled={stale || page.offset === 0}
                  onClick={() =>
                    navigate({
                      ...state,
                      offset: Math.max(0, page.offset - page.limit),
                      selected: null,
                    })
                  }
                >
                  ← Назад
                </button>
                <div className="discover-pagination-position">
                  {stale ? (
                    <DiscoverPaginationLoading mode={loadingMode} slow={slow} />
                  ) : (
                    <span>
                      Страница {Math.floor(page.offset / page.limit) + 1}
                    </span>
                  )}
                </div>
                <button
                  disabled={stale || page.next_offset === null}
                  onClick={() =>
                    page.next_offset !== null &&
                    navigate({
                      ...state,
                      offset: page.next_offset,
                      selected: null,
                    })
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
                      const button =
                        resultsPanel.current?.querySelector<HTMLButtonElement>(
                          '[aria-pressed="true"]',
                        );
                      selectIdentity(null);
                      requestAnimationFrame(() => {
                        button?.focus();
                        button?.scrollIntoView({
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
                    {applicationId != null && applicationStatus != null ? (
                      <div role="status">
                        {savedNow !== undefined && (
                          <p className="badge saved-badge">
                            {savedNow
                              ? "Вакансия сохранена"
                              : "Вакансия уже была в ваших откликах"}
                          </p>
                        )}
                        <p>
                          Текущий статус:{" "}
                          {statusLabels[applicationStatus] ?? applicationStatus}
                        </p>
                        <Link
                          className="primary button-link"
                          href={`/applications/${applicationId}?from=${encodeURIComponent(currentUrl)}`}
                          prefetch={false}
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
        </section>
      )}
    </>
  );
}
