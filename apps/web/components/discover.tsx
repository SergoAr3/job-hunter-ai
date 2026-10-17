"use client";
import { useEffect, useId, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  discoverUrl,
  parseDiscoverState,
  vacancyKey as keyOf,
} from "../lib/discover";
import { DiscoverRoleSuggestions } from "./discover-role-suggestions";
import { DiscoverLoading, DiscoverPaginationLoading } from "./discover-loading";
import Link from "next/link";
import type { SaveResult, Vacancy } from "../lib/contracts";
import { webRequest } from "../lib/client";
import { errorMessage } from "../lib/errors";
import { isFeed, useDiscoverResults } from "./use-discover-results";
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
  const {
    page,
    currentPage,
    error: failure,
    navigationError,
    loading,
    slow,
    stale,
    loadingMode,
    generation,
    submit,
    next,
    navigate,
    retry,
    refresh,
    restart,
    recordSave,
    requestKey,
  } = useDiscoverResults(state, draft.revision);
  const error = failure ? errorMessage(failure) : "";
  const unavailable = failure?.code === "feed_expired";
  const feedPage = page && isFeed(page) ? page : null;
  const legacyPage = page && !isFeed(page) ? page : null;
  const loadingId = useId();
  const selected =
    currentPage?.items.find((item) => keyOf(item) === state.selected) ?? null;
  const [saveErrors, setSaveErrors] = useState<Record<string, string>>({});
  const [saveFeedback, setSaveFeedback] = useState<Record<string, boolean>>({});
  const [pending, setPending] = useState<string | null>(null);
  const saveLock = useRef(false);
  const detailPanel = useRef<HTMLElement | null>(null);
  const resultsPanel = useRef<HTMLDivElement | null>(null);
  const navigationMessage = useRef<HTMLParagraphElement | null>(null);
  const forwardButton = useRef<HTMLButtonElement | null>(null);
  const forwardFocus = useRef<{
    button: HTMLButtonElement;
    url: string;
  } | null>(null);
  useEffect(() => {
    // A disabled CTA may blur to body while waiting. Remember actual focus
    // moves (including moves followed by blur) so completion cannot steal it.
    const cancel = () => {
      forwardFocus.current = null;
    };
    const moved = (event: FocusEvent) => {
      if (
        event.target &&
        event.target !== document.body &&
        event.target !== document.documentElement &&
        event.target !== forwardFocus.current?.button
      )
        cancel();
    };
    const clickedElsewhere = (event: PointerEvent) => {
      if (
        forwardFocus.current &&
        !forwardFocus.current.button.contains(event.target as Node | null)
      )
        cancel();
    };
    document.addEventListener("focusin", moved);
    document.addEventListener("pointerdown", clickedElsewhere);
    window.addEventListener("blur", cancel);
    return () => {
      document.removeEventListener("focusin", moved);
      document.removeEventListener("pointerdown", clickedElsewhere);
      window.removeEventListener("blur", cancel);
    };
  }, []);
  useEffect(() => {
    const origin = forwardFocus.current;
    if (!origin) return;
    if (origin.url !== searchUrl) {
      forwardFocus.current = null;
      return;
    }
    if (loading) return;
    forwardFocus.current = null;
    if (
      currentPage &&
      isFeed(currentPage) &&
      !navigationError &&
      (document.activeElement === document.body ||
        document.activeElement === document.documentElement ||
        document.activeElement === null)
    ) {
      if (
        currentPage.next_action === "continue" &&
        forwardButton.current &&
        !forwardButton.current.disabled
      )
        forwardButton.current.focus();
      else if (currentPage.next_action === null && !origin.button.isConnected)
        navigationMessage.current?.focus();
    }
  }, [loading, currentPage, searchUrl, navigationError]);
  const focusedResults = useRef<string | null>(null);
  useEffect(() => {
    if (!currentPage) return;
    if (
      focusedResults.current &&
      focusedResults.current !== searchUrl &&
      state.mode === "feed" &&
      !state.selected
    )
      resultsPanel.current?.focus();
    focusedResults.current = searchUrl;
  }, [currentPage, searchUrl, state.selected, state.mode]);
  useEffect(() => {
    if (raw !== (currentUrl.split("?")[1] ?? ""))
      router.replace(currentUrl, { scroll: false });
  }, [raw, currentUrl, router]);
  const { query: confirmedQuery, remote: confirmedRemote } = state;
  useEffect(() => {
    function handlePageShow(event: PageTransitionEvent) {
      if (event.persisted) refresh();
    }
    window.addEventListener("pageshow", handlePageShow);
    return () => window.removeEventListener("pageshow", handlePageShow);
  }, [refresh]);

  const visibleSelection = selected ? keyOf(selected) : null;
  useEffect(() => {
    if (visibleSelection && window.innerWidth <= 900) {
      detailPanel.current?.focus();
      detailPanel.current?.scrollIntoView({ block: "start" });
    }
  }, [visibleSelection]);

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
    const feedbackKey = `${requestKey}:${key}`;
    setSaveErrors((previous) => ({ ...previous, [feedbackKey]: "" }));
    try {
      const { source, source_scope, external_id } = item;
      const result = await webRequest<SaveResult>("/api/discover/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source, source_scope, external_id }),
      });
      recordSave(key, result);
      setSaveFeedback((previous) => ({
        ...previous,
        [feedbackKey]: result.application_created,
      }));
    } catch (error) {
      if (generation.current === current)
        setSaveErrors((previous) => ({
          ...previous,
          [feedbackKey]: errorMessage(error),
        }));
    } finally {
      saveLock.current = false;
      setPending(null);
    }
  }
  const selectionKey = selected ? keyOf(selected) : "";
  const savedNow = saveFeedback[`${requestKey}:${selectionKey}`];
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
          if (query.trim()) submit(query.trim(), remote);
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
              ? loadingMode === "replay"
                ? "Загружаем…"
                : "Ищем…"
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
      <div>
        {error && (
          <div className="notice error" role="alert">
            <p>{unavailable ? "Эти результаты больше недоступны." : error}</p>
            <button onClick={unavailable ? restart : retry}>
              {unavailable || !state.mode ? "Повторить поиск" : "Повторить"}
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
                Предыдущие результаты
                {legacyPage && !state.mode ? " · страница " : ""}
                {legacyPage && !state.mode
                  ? Math.floor(legacyPage.offset / legacyPage.limit) + 1
                  : ""}
              </span>
            )}
            <span>
              {page.returned_count} на странице
              {legacyPage?.locally_filtered ? " · фильтр удалённой работы" : ""}
            </span>
          </div>
          <div className={`discover-grid ${selected ? "has-selection" : ""}`}>
            <div className="results-list" ref={resultsPanel} tabIndex={-1}>
              {page.items.length === 0 && !feedPage && (
                <div className="state">
                  <h3>На этой странице ничего не найдено</h3>
                  <p>
                    {legacyPage?.next_offset !== null
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
              {feedPage ? (
                <div className="feed-navigation">
                  {!stale && feedPage.next_action !== "next" && (
                    <p
                      ref={navigationMessage}
                      role="status"
                      aria-live="polite"
                      tabIndex={-1}
                      className="feed-navigation-message"
                    >
                      {feedPage.next_action === "continue"
                        ? feedPage.items.length
                          ? "Пока не нашли новые удалённые вакансии."
                          : "Пока не нашли удалённые вакансии."
                        : feedPage.next_action === null
                          ? feedPage.items.length
                            ? "Больше удалённых вакансий не найдено."
                            : "Удалённые вакансии по этому запросу не найдены."
                          : ""}
                    </p>
                  )}
                  {navigationError && (
                    <div className="feed-navigation-error" role="alert">
                      <p>
                        {navigationError.code === "feed_expired"
                          ? "Эти результаты больше недоступны."
                          : "Не удалось продолжить поиск."}
                      </p>
                      <button
                        disabled={stale}
                        onClick={
                          navigationError.code === "feed_expired"
                            ? restart
                            : retry
                        }
                      >
                        {navigationError.code === "feed_expired"
                          ? "Повторить поиск"
                          : "Повторить"}
                      </button>
                    </div>
                  )}
                  <nav
                    className="pagination discover-pagination"
                    aria-label="Страницы результатов"
                  >
                    <button
                      disabled={stale || !feedPage.prev_cursor}
                      onClick={() =>
                        feedPage.prev_cursor &&
                        navigate({
                          ...state,
                          cursor: feedPage.prev_cursor,
                          selected: null,
                        })
                      }
                    >
                      ← Назад
                    </button>
                    <div className="discover-pagination-position">
                      {stale && (
                        <DiscoverPaginationLoading
                          mode={loadingMode}
                          slow={slow}
                        />
                      )}
                    </div>
                    {feedPage.next_action && feedPage.next_cursor && (
                      <button
                        ref={forwardButton}
                        disabled={stale}
                        onClick={(event) => {
                          forwardFocus.current =
                            document.activeElement === event.currentTarget
                              ? { button: event.currentTarget, url: searchUrl }
                              : null;
                          next();
                        }}
                      >
                        {feedPage.next_action === "continue"
                          ? "Продолжить поиск"
                          : "Далее →"}
                      </button>
                    )}
                  </nav>
                </div>
              ) : (
                legacyPage && (
                  <nav
                    className="pagination discover-pagination"
                    aria-label="Страницы результатов"
                  >
                    <button
                      disabled={stale || legacyPage.offset === 0}
                      onClick={() =>
                        navigate({
                          ...state,
                          offset: Math.max(
                            0,
                            legacyPage.offset - legacyPage.limit,
                          ),
                          selected: null,
                        })
                      }
                    >
                      ← Назад
                    </button>
                    <div className="discover-pagination-position">
                      {stale ? (
                        <DiscoverPaginationLoading
                          mode={loadingMode}
                          slow={slow}
                        />
                      ) : (
                        <span>
                          Страница{" "}
                          {Math.floor(legacyPage.offset / legacyPage.limit) + 1}
                        </span>
                      )}
                    </div>
                    <button
                      disabled={stale || legacyPage.next_offset === null}
                      onClick={() =>
                        legacyPage.next_offset !== null &&
                        navigate({
                          ...state,
                          offset: legacyPage.next_offset,
                          selected: null,
                        })
                      }
                    >
                      Далее →
                    </button>
                  </nav>
                )
              )}
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
                    {saveErrors[`${requestKey}:${selectionKey}`] && (
                      <p className="notice error" role="alert">
                        {saveErrors[`${requestKey}:${selectionKey}`]}
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
