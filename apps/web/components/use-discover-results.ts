"use client";
import { useEffect, useRef, useState } from "react";
import type {
  DiscoverFeedPage,
  DiscoverPage,
  SaveResult,
  Vacancy,
} from "../lib/contracts";
import {
  discoverUrl,
  parseDiscoverState,
  vacancyKey,
  type DiscoverState,
} from "../lib/discover";
import { webRequest } from "../lib/client";
import {
  advanceFeed,
  replayFeed,
  startFeed,
} from "../lib/discover-feed-client";
import { WebError } from "../lib/errors";

export type SearchPage = DiscoverPage | DiscoverFeedPage;
export const isFeed = (page: SearchPage): page is DiscoverFeedPage =>
  "cursor" in page;
function reconcile(item: Vacancy, result?: SaveResult): Vacancy {
  if (!result || item.application_id != null) return item;
  return {
    ...item,
    already_saved_for_user: true,
    application_id: result.application.id,
    application_status: result.application
      .status as Vacancy["application_status"],
  };
}
interface Loaded {
  url: string;
  activation: number;
  visit: number;
  page: SearchPage | null;
  error: WebError | null;
}
type StartAttempt = {
  query: string;
  url: string;
  keepUrl: boolean;
  retryCursor?: string;
};

export function useDiscoverResults(state: DiscoverState, activation: number) {
  const url = discoverUrl({ ...state, selected: null });
  const [visit, setVisit] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [pending, setPending] = useState<{
    id: number;
    url: string;
    kind: "start" | "advance";
  } | null>(null);
  const [actionError, setActionError] = useState<{
    url: string;
    error: WebError;
  } | null>(null);
  const generation = useRef(0);
  const activeUrl = useRef({ url, activation });
  useEffect(() => {
    activeUrl.current = { url, activation };
  }, [url, activation]);
  const seeded = useRef<{ url: string; visit: number } | null>(null);
  const startAttempt = useRef<StartAttempt | null>(null);
  const saves = useRef<Record<string, SaveResult>>({});
  const request = useRef<{
    url: string;
    visit: number;
    id: number;
    kind: "load" | "start" | "advance";
    controller: AbortController;
    promise: Promise<SearchPage>;
  } | null>(null);
  const mounted = useRef(0);
  const invalidate = () => {
    generation.current++;
    request.current?.controller.abort();
    request.current = null;
    seeded.current = null;
  };
  useEffect(() => {
    mounted.current++;
    const pop = () => {
      // Next may publish the URL and start its replay before our popstate
      // listener runs. Do not invalidate that GET a second time.
      if (request.current && request.current.kind !== "load") {
        generation.current++;
        request.current.controller.abort();
        request.current = null;
      }
      seeded.current = null;
      startAttempt.current = null;
      setPending(null);
      setActionError(null);
    };
    window.addEventListener("popstate", pop);
    const stopIfUnmounted = () => {
      if (!mounted.current) {
        generation.current++;
        request.current?.controller.abort();
        request.current = null;
      }
    };
    const release = () => {
      mounted.current--;
    };
    return () => {
      window.removeEventListener("popstate", pop);
      release();
      queueMicrotask(stopIfUnmounted);
    };
  }, []);
  useEffect(() => {
    if (seeded.current?.url === url && seeded.current.visit === visit) return;
    seeded.current = null;
    if (request.current?.kind === "start" && request.current.url === url)
      return;
    if (!state.query || state.mode === "start") return;
    const id = ++generation.current;
    let active = true;
    saves.current = {};
    if (
      request.current?.kind !== "load" ||
      request.current.url !== url ||
      request.current.visit !== visit
    ) {
      request.current?.controller.abort();
      const controller = new AbortController();
      const params = new URLSearchParams({
        query: state.query,
        remote_only: String(state.remote),
        offset: String(state.offset),
      });
      request.current = {
        url,
        visit,
        id,
        kind: "load",
        controller,
        promise:
          state.mode === "feed" && state.cursor
            ? replayFeed(state.cursor, controller.signal)
            : webRequest<DiscoverPage>(`/api/discover?${params}`, {
                signal: controller.signal,
              }),
      };
    }
    const current = request.current;
    void current.promise
      .then((page) => {
        if (!active || generation.current !== id) return;
        setLoaded({
          url,
          activation,
          visit,
          page: {
            ...page,
            items: page.items.map((item) =>
              reconcile(item, saves.current[vacancyKey(item)]),
            ),
          },
          error: null,
        });
        saves.current = {};
      })
      .catch((error) => {
        if (active && generation.current === id)
          setLoaded({
            url,
            activation,
            visit,
            page: null,
            error:
              error instanceof WebError
                ? error
                : new WebError("api_unavailable"),
          });
      })
      .finally(() => {
        if (request.current === current) request.current = null;
      });
    return () => {
      active = false;
    };
  }, [
    url,
    visit,
    state.query,
    state.remote,
    state.offset,
    state.mode,
    state.cursor,
    activation,
  ]);

  const current =
    loaded?.url === url &&
    loaded.visit === visit &&
    loaded.activation === activation
      ? loaded
      : null;
  const mutation = pending?.url === url ? pending : null;
  const loading =
    !!mutation || (!!state.query && state.mode !== "start" && !current);
  const page = current?.page ?? (loading ? loaded?.page : null) ?? null;
  const currentPage = loading ? null : (current?.page ?? null);
  const error = loading ? null : (current?.error ?? null);
  const navigationError = actionError?.url === url ? actionError.error : null;
  const requestKey = `${url}:${activation}:${visit}:${mutation?.id ?? "load"}`;
  const [slowKey, setSlowKey] = useState<string | null>(null);
  useEffect(() => {
    if (!loading) return;
    let active = true;
    const timer = window.setTimeout(() => {
      if (active) setSlowKey(requestKey);
    }, 2800);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [loading, requestKey]);
  const replaying = state.mode === "feed" && !mutation;
  const previousState = loaded
    ? parseDiscoverState(new URLSearchParams(loaded.url.split("?")[1]))
    : null;
  const sameLegacySearch =
    !state.mode &&
    previousState &&
    !previousState.mode &&
    previousState.query === state.query &&
    previousState.remote === state.remote;
  const loadingMode: "initial" | "next" | "previous" | "refresh" | "replay" =
    replaying
      ? "replay"
      : !page
        ? "initial"
        : mutation?.kind === "advance"
          ? "next"
          : !isFeed(page) && sameLegacySearch && state.offset > page.offset
            ? "next"
            : !isFeed(page) && sameLegacySearch && state.offset < page.offset
              ? "previous"
              : "refresh";

  function install(
    page: DiscoverFeedPage,
    next: DiscoverState,
    replace: boolean,
  ) {
    const nextUrl = discoverUrl({ ...next, selected: null });
    seeded.current = { url: nextUrl, visit };
    setLoaded({
      url: nextUrl,
      activation:
        activeUrl.current.activation +
        (activeUrl.current.url === nextUrl ? 0 : 1),
      visit,
      page: {
        ...page,
        items: page.items.map((item) =>
          reconcile(item, saves.current[vacancyKey(item)]),
        ),
      },
      error: null,
    });
    setPending(null);
    setActionError(null);
    startAttempt.current = null;
    saves.current = {};
    window.history[replace ? "replaceState" : "pushState"](
      null,
      "",
      discoverUrl(next),
    );
  }
  function start(query: string, keepUrl = false, retryCursor?: string) {
    const target = keepUrl
      ? url
      : discoverUrl({
          query,
          remote: true,
          offset: 0,
          selected: null,
          mode: "start",
        });
    if (
      request.current?.kind === "start" &&
      request.current.url === target &&
      startAttempt.current?.query === query
    )
      return;
    invalidate();
    const id = generation.current;
    const controller = new AbortController();
    startAttempt.current = { query, url: target, keepUrl, retryCursor };
    saves.current = {};
    setPending({ id, url: target, kind: "start" });
    setActionError(null);
    if (!keepUrl && target !== url) window.history.pushState(null, "", target);
    const promise = retryCursor
      ? advanceFeed(retryCursor, controller.signal)
      : startFeed(query, controller.signal);
    const currentRequest = {
      url: target,
      visit,
      id,
      kind: "start" as const,
      controller,
      promise,
    };
    request.current = currentRequest;
    void promise
      .then((page) => {
        if (generation.current !== id) return;
        install(
          page,
          {
            query,
            remote: true,
            offset: 0,
            selected: null,
            mode: "feed",
            cursor: page.cursor,
          },
          true,
        );
      })
      .catch((error) => {
        if (generation.current !== id) return;
        const failure =
          error instanceof WebError ? error : new WebError("feed_unavailable");
        startAttempt.current = {
          query,
          url: target,
          keepUrl,
          retryCursor: failure.retryCursor ?? retryCursor,
        };
        setLoaded({
          url: target,
          activation:
            activeUrl.current.activation +
            (activeUrl.current.url === target ? 0 : 1),
          visit,
          page: null,
          error: failure,
        });
        setPending(null);
      })
      .finally(() => {
        if (request.current === currentRequest) request.current = null;
      });
  }
  function submit(query: string, remote: boolean) {
    if (remote) {
      start(query);
      return;
    }
    invalidate();
    startAttempt.current = null;
    setPending(null);
    setActionError(null);
    const next = discoverUrl({
      query,
      remote: false,
      offset: 0,
      selected: null,
    });
    if (next === url) setVisit((value) => value + 1);
    else window.history.pushState(null, "", next);
  }
  function next() {
    if (
      !currentPage ||
      !isFeed(currentPage) ||
      !currentPage.next_cursor ||
      request.current?.kind === "advance"
    )
      return;
    const from = currentPage;
    invalidate();
    const id = generation.current;
    const controller = new AbortController();
    setPending({ id, url, kind: "advance" });
    setActionError(null);
    const promise = advanceFeed(currentPage.next_cursor, controller.signal);
    const currentRequest = {
      url,
      visit,
      id,
      kind: "advance" as const,
      controller,
      promise,
    };
    request.current = currentRequest;
    void promise
      .then((page) => {
        if (generation.current !== id) return;
        if (page.state === "page")
          install(
            page,
            {
              ...state,
              mode: "feed",
              cursor: page.cursor,
              offset: 0,
              selected: null,
            },
            false,
          );
        else {
          setLoaded((latest) => {
            if (
              generation.current !== id ||
              latest?.url !== url ||
              latest.activation !== activation ||
              latest.visit !== visit ||
              !latest.page ||
              !isFeed(latest.page) ||
              latest.page.cursor !== from.cursor
            )
              return latest;
            // Paused/end retain the visible page, including concurrent Save
            // reconciliation. Only the continuation links change.
            return {
              ...latest,
              page: {
                ...latest.page,
                next_cursor: page.next_cursor,
                next_action: page.next_action,
              },
              error: null,
            };
          });
          setPending(null);
        }
      })
      .catch((error) => {
        if (generation.current !== id) return;
        setPending(null);
        setActionError({
          url,
          error:
            error instanceof WebError
              ? error
              : new WebError("feed_unavailable"),
        });
      })
      .finally(() => {
        if (request.current === currentRequest) request.current = null;
      });
  }
  function navigate(next: DiscoverState) {
    invalidate();
    startAttempt.current = null;
    setPending(null);
    setActionError(null);
    const nextUrl = discoverUrl(next);
    if (discoverUrl({ ...next, selected: null }) === url)
      setVisit((value) => value + 1);
    if (nextUrl !== discoverUrl(state))
      window.history.pushState(null, "", nextUrl);
  }
  function retry() {
    const attempt = startAttempt.current;
    if (attempt?.url === url)
      start(attempt.query, attempt.keepUrl, attempt.retryCursor);
    else if (navigationError) next();
    else {
      invalidate();
      setVisit((value) => value + 1);
    }
  }
  function recordSave(key: string, result: SaveResult) {
    saves.current[key] = result;
    setLoaded(
      (old) =>
        old && {
          ...old,
          page: old.page && {
            ...old.page,
            items: old.page.items.map((item) =>
              vacancyKey(item) === key ? reconcile(item, result) : item,
            ),
          },
        },
    );
  }
  return {
    page,
    currentPage,
    error,
    navigationError,
    loading,
    stale: loading && !!page,
    slow: loading && slowKey === requestKey && !replaying,
    loadingMode,
    requestKey,
    generation,
    submit,
    next,
    navigate,
    retry,
    refresh: () => {
      invalidate();
      setPending(null);
      setActionError(null);
      setVisit((value) => value + 1);
    },
    restart: () => start(state.query, true),
    recordSave,
  };
}
