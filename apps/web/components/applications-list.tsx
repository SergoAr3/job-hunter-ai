"use client";
import { ListboxSelect } from "./listbox-select";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  applicationStatuses,
  applicationsUrl,
  APPLICATIONS_PAGE_SIZE,
  MAX_APPLICATIONS_OFFSET,
  parseApplicationsState,
  type ApplicationsState,
} from "../lib/applications";
import type { ApplicationListItem, ApplicationsPage } from "../lib/contracts";
import { webRequest } from "../lib/client";
import { errorMessage } from "../lib/errors";
import { compactLocation } from "../lib/presentation";
import { statusLabels, workplace } from "./vacancy";

const sortLabels = {
  newest: "Сначала новые",
  oldest: "Сначала старые",
  next_action: "По следующему действию",
};

type EmptyPageRollback = { filter: string; nextOffset: number };

function rowName(item: ApplicationListItem, items: ApplicationListItem[]) {
  const title = item.title?.trim() || "Вакансия без названия";
  const company = item.company?.trim() || "Компания не указана";
  const location = compactLocation(item.location);
  const base = [title, company, location].filter(Boolean).join(" — ");
  const duplicates = items.filter(
    (other) =>
      (other.title?.trim() || "Вакансия без названия") === title &&
      (other.company?.trim() || "Компания не указана") === company &&
      compactLocation(other.location) === location,
  );
  if (base.length > 140)
    return `${base.slice(0, 137)}… — запись ${item.app_id}`;
  return duplicates.length > 1 ? `${base} — запись ${item.app_id}` : base;
}

function savedDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString("ru-RU");
}

export function ApplicationsList() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const rollbackRef = useRef<EmptyPageRollback | null>(null);
  const raw = searchParams.toString();
  const state = parseApplicationsState(new URLSearchParams(raw));
  const currentUrl = applicationsUrl(state);
  useEffect(() => {
    if (
      raw &&
      `?${raw}` !==
        (currentUrl.split("?")[1] ? `?${currentUrl.split("?")[1]}` : "")
    )
      router.replace(currentUrl);
  }, [raw, currentUrl, router]);
  if (
    raw &&
    `?${raw}` !==
      (currentUrl.split("?")[1] ? `?${currentUrl.split("?")[1]}` : "")
  )
    return (
      <div className="state" role="status">
        Открываем список…
      </div>
    );
  return (
    <ApplicationsContent
      key={currentUrl}
      state={state}
      currentUrl={currentUrl}
      rollbackRef={rollbackRef}
    />
  );
}

function ApplicationsContent({
  state,
  currentUrl,
  rollbackRef,
}: {
  state: ApplicationsState;
  currentUrl: string;
  rollbackRef: { current: EmptyPageRollback | null };
}) {
  const router = useRouter();
  const { q, status, sort, offset } = state;
  const [draft, setDraft] = useState(state.q);
  const [page, setPage] = useState<ApplicationsPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({
      limit: String(APPLICATIONS_PAGE_SIZE),
      offset: String(offset),
    });
    if (q) params.set("q", q);
    if (status) params.set("status", status);
    params.set("sort", sort);
    void webRequest<ApplicationsPage>(`/api/applications?${params}`, {
      signal: controller.signal,
    })
      .then((result) => {
        if (controller.signal.aborted) return;
        if (
          !Array.isArray(result.items) ||
          typeof result.has_next !== "boolean"
        )
          throw new Error("Malformed page");
        if (offset > 0 && result.items.length === 0) {
          const filter = applicationsUrl({ q, status, sort, offset: 0 });
          const nextOffset =
            rollbackRef.current?.filter === filter &&
            rollbackRef.current.nextOffset === offset
              ? 0
              : offset - APPLICATIONS_PAGE_SIZE;
          rollbackRef.current = nextOffset > 0 ? { filter, nextOffset } : null;
          router.replace(
            applicationsUrl({
              q,
              status,
              sort,
              offset: nextOffset,
            }),
          );
          return;
        }
        rollbackRef.current = null;
        setPage(result);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [q, status, sort, offset, retry, router, rollbackRef]);

  function navigate(changes: Partial<ApplicationsState>) {
    router.push(applicationsUrl({ ...state, ...changes }));
  }

  return (
    <>
      <header className="page-heading">
        <span className="eyebrow">ВАША РАБОТА С ВАКАНСИЯМИ</span>
        <h1>Мои вакансии</h1>
        <p>Сохранённые вакансии и текущий статус каждого отклика.</p>
      </header>
      <div className="applications-controls">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            navigate({ q: draft.trim(), offset: 0 });
          }}
        >
          <label htmlFor="applications-search">Название или компания</label>
          <div className="applications-search-row">
            <input
              id="applications-search"
              value={draft}
              maxLength={100}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Поиск по вакансиям"
            />
            <button className="primary" type="submit">
              Найти
            </button>
          </div>
        </form>
        <div className="applications-control">
          <label id="applications-status-label" htmlFor="applications-status">
            Статус
          </label>
          <ListboxSelect<string>
            id="applications-status"
            labelId="applications-status-label"
            value={state.status ?? ""}
            options={["", ...applicationStatuses]}
            labels={Object.assign({ "": "Все статусы" }, statusLabels)}
            disabled={false}
            onChange={(value) =>
              navigate({
                status: (value || null) as ApplicationsState["status"],
                offset: 0,
              })
            }
          />
        </div>
        <div className="applications-control">
          <label id="applications-sort-label" htmlFor="applications-sort">
            Сортировка
          </label>
          <ListboxSelect
            id="applications-sort"
            labelId="applications-sort-label"
            value={state.sort}
            options={Object.keys(sortLabels) as ApplicationsState["sort"][]}
            labels={sortLabels}
            disabled={false}
            onChange={(value) => navigate({ sort: value, offset: 0 })}
          />
        </div>
      </div>
      {loading && (
        <div className="state" role="status">
          Загружаем вакансии…
        </div>
      )}
      {error && (
        <div className="notice error" role="alert">
          <p>{error}</p>
          <button
            type="button"
            onClick={() => {
              setLoading(true);
              setError("");
              setRetry((value) => value + 1);
            }}
          >
            Повторить
          </button>
        </div>
      )}
      {page && (
        <>
          {page.items.length === 0 ? (
            <div className="state">
              <h2>
                {state.q || state.status
                  ? "По выбранным условиям ничего не найдено"
                  : "Сохранённых вакансий пока нет"}
              </h2>
              <p>
                {state.q || state.status
                  ? "Измените поиск или фильтр."
                  : "Найдите вакансию и сохраните её, чтобы вернуться сюда."}
              </p>
              {state.q || state.status ? (
                <Link href="/applications">Сбросить поиск и фильтр</Link>
              ) : (
                <Link href="/discover">Перейти к поиску вакансий</Link>
              )}
            </div>
          ) : (
            <div
              className="applications-rows"
              aria-label="Сохранённые вакансии"
            >
              {page.items.map((item) => {
                const location = compactLocation(item.location);
                const date = savedDate(item.created_at);
                return (
                  <Link
                    className="application-row"
                    href={`/applications/${item.app_id}?from=${encodeURIComponent(currentUrl)}`}
                    aria-label={rowName(item, page.items)}
                    aria-describedby={
                      item.next_action
                        ? `application-next-action-${item.app_id}`
                        : undefined
                    }
                    key={item.app_id}
                  >
                    <span className="application-row-main">
                      <strong>
                        {item.title?.trim() || "Вакансия без названия"}
                      </strong>
                      <span className="company">
                        {item.company?.trim() || "Компания не указана"}
                      </span>
                      {item.next_action && (
                        <span
                          id={`application-next-action-${item.app_id}`}
                          className="application-row-next-action"
                          title={item.next_action}
                        >
                          Следующее: {item.next_action}
                        </span>
                      )}
                    </span>
                    <span className="application-row-meta">
                      {location && (
                        <span title={item.location ?? undefined}>
                          {location}
                        </span>
                      )}
                      {workplace[item.workplace_type] && (
                        <span className="workplace-pill">
                          {workplace[item.workplace_type]}
                        </span>
                      )}
                      {date && <span>Сохранена {date}</span>}
                    </span>
                    <span className="badge status-badge">
                      {statusLabels[item.status] ?? item.status}
                    </span>
                  </Link>
                );
              })}
            </div>
          )}
          <nav
            className="applications-pagination"
            aria-label="Страницы вакансий"
          >
            {state.offset > 0 ? (
              <Link
                href={applicationsUrl({
                  ...state,
                  offset: Math.max(0, state.offset - APPLICATIONS_PAGE_SIZE),
                })}
              >
                Назад
              </Link>
            ) : (
              <span />
            )}
            <span>
              Страница {Math.floor(state.offset / APPLICATIONS_PAGE_SIZE) + 1}
            </span>
            {page.has_next &&
            state.offset <= MAX_APPLICATIONS_OFFSET - APPLICATIONS_PAGE_SIZE ? (
              <Link
                href={applicationsUrl({
                  ...state,
                  offset: state.offset + APPLICATIONS_PAGE_SIZE,
                })}
              >
                Вперёд
              </Link>
            ) : (
              <span />
            )}
          </nav>
        </>
      )}
    </>
  );
}
