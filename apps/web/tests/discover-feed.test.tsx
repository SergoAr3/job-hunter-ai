import { StrictMode } from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Discover } from "../components/discover";
import { feedPage, page, saved, vacancy } from "./fixtures";
import type { DiscoverFeedPage } from "../lib/contracts";
import { vacancyKey } from "../lib/discover";
import {
  back,
  forward,
  navigationUrl,
  resetNavigation,
  testRouter,
} from "./discover-navigation";
vi.mock("next/navigation", async () => {
  const { testRouter, useTestSearchParams } =
    await import("./discover-navigation");
  return { useRouter: () => testRouter, useSearchParams: useTestSearchParams };
});
vi.mock("../components/discover-role-suggestions", () => ({
  DiscoverRoleSuggestions: () => null,
}));
const A = "A".repeat(32),
  B = "B".repeat(32),
  C = "C".repeat(32),
  D = "D".repeat(32);
const url = (cursor = A, query = "Python", selected?: string) =>
  `/discover?${new URLSearchParams({ query, remote_only: "true", cursor, ...(selected ? { selected } : {}) })}`;
const first = { ...feedPage, cursor: A, next_cursor: B };
const second: DiscoverFeedPage = {
  ...first,
  cursor: C,
  prev_cursor: A,
  next_cursor: D,
  items: [{ ...first.items[0], external_id: "page2", title: "Second vacancy" }],
};
const paused: DiscoverFeedPage = {
  ...first,
  cursor: B,
  prev_cursor: A,
  next_cursor: C,
  next_action: "continue",
  state: "paused",
  items: [],
  returned_count: 0,
};
const end: DiscoverFeedPage = {
  ...paused,
  state: "end",
  next_cursor: null,
  next_action: null,
};
const fetcher = vi.fn();
const calls = () =>
  fetcher.mock.calls.filter(([path]) =>
    String(path).startsWith("/api/discover/feed"),
  );
const posts = () => calls().filter(([, init]) => init?.method === "POST");
const gets = () =>
  calls().filter(([, init]) => !init?.method || init.method === "GET");
const card = () =>
  screen.getByRole("button", { name: /Python developer.*Test company/ });
function submit(query = "Python", remote = true) {
  fireEvent.change(screen.getByLabelText("Должность или ключевые слова"), {
    target: { value: query },
  });
  const toggle = screen.getByLabelText(
    "Только удалённая работа",
  ) as HTMLInputElement;
  if (toggle.checked !== remote) fireEvent.click(toggle);
  fireEvent.click(screen.getByRole("button", { name: "Найти вакансии" }));
}
function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => (resolve = done));
  return { promise, resolve };
}
async function finish(pending: ReturnType<typeof deferred>, value: unknown) {
  await act(async () => pending.resolve(Response.json(value)));
}
beforeEach(() => {
  resetNavigation(url());
  fetcher
    .mockReset()
    .mockImplementation((path: string, init?: RequestInit) =>
      Promise.resolve(
        Response.json(
          path.startsWith("/api/discover/feed")
            ? init?.method === "POST"
              ? second
              : first
            : page,
        ),
      ),
    );
  vi.stubGlobal("fetch", fetcher);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 0;
  });
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("starts once in StrictMode/double submit, replaces the candidate URL and seeds without GET", async () => {
  resetNavigation();
  const pending = deferred();
  fetcher.mockReturnValue(pending.promise);
  render(
    <StrictMode>
      <Discover />
    </StrictMode>,
  );
  submit();
  fireEvent.submit(
    screen.getByRole("button", { name: "Найти вакансии" }).closest("form")!,
  );
  expect(posts()).toHaveLength(1);
  expect(gets()).toHaveLength(0);
  expect(screen.getByRole("status")).toHaveTextContent(
    "Ищем подходящие вакансии",
  );
  expect(navigationUrl()).toBe("/discover?query=Python&remote_only=true");
  await finish(pending, first);
  expect(card()).toBeEnabled();
  expect(navigationUrl()).toBe(url());
  expect(posts()).toHaveLength(1);
  expect(gets()).toHaveLength(0);
  expect(testRouter.push).toHaveBeenCalledTimes(1);
  expect(testRouter.replace).toHaveBeenCalledTimes(1);
  expect(screen.queryByText(/Страница \d/)).not.toBeInTheDocument();
});

it.each(["paused", "end"] as const)(
  "canonicalizes an initial %s and replays it without hidden start",
  async (state) => {
    resetNavigation("/discover?query=Python&remote_only=true");
    const value: DiscoverFeedPage = {
      ...paused,
      cursor: A,
      prev_cursor: null,
      state,
      next_action: state === "paused" ? "continue" : null,
      next_cursor: state === "paused" ? B : null,
    };
    fetcher.mockResolvedValue(Response.json(value));
    const view = render(<Discover />);
    await act(async () => {});
    expect(calls()).toHaveLength(0);
    submit();
    await screen.findByText(
      state === "paused"
        ? "Пока не нашли удалённые вакансии."
        : "Удалённые вакансии по этому запросу не найдены.",
    );
    expect(navigationUrl()).toBe(url());
    expect(posts()).toHaveLength(1);
    expect(gets()).toHaveLength(0);
    view.unmount();
    fetcher.mockImplementation(() => Promise.resolve(Response.json(value)));
    render(<Discover />);
    await screen.findByText(
      state === "paused"
        ? "Пока не нашли удалённые вакансии."
        : "Удалённые вакансии по этому запросу не найдены.",
    );
    expect(posts()).toHaveLength(1);
    expect(gets()).toHaveLength(1);
  },
);

it.each(["next", "continue", null] as const)(
  "uses next_action=%s directly from one replay and restores selected identity",
  async (action) => {
    resetNavigation(url(A, "Python", vacancyKey(vacancy)));
    fetcher.mockResolvedValue(
      Response.json({
        ...first,
        next_action: action,
        next_cursor: action ? D : null,
      }),
    );
    render(
      <StrictMode>
        <Discover />
      </StrictMode>,
    );
    await screen.findByLabelText("Описание вакансии");
    expect(gets()).toHaveLength(1);
    expect(posts()).toHaveLength(0);
    if (action)
      expect(
        screen.getByRole("button", {
          name: action === "next" ? "Далее →" : "Продолжить поиск",
        }),
      ).toBeEnabled();
    else
      expect(
        screen.queryByRole("button", { name: "Далее →" }),
      ).not.toBeInTheDocument();
    expect(screen.queryByText(/Страница \d/)).not.toBeInTheDocument();
    expect(
      screen.queryByText("Источник отвечает медленнее обычного"),
    ).not.toBeInTheDocument();
  },
);

it("keeps the visible URL/cards pending, pushes a page once, and Previous/Back/Forward each GET once", async () => {
  const pending = deferred();
  fetcher.mockImplementation((path: string, init?: RequestInit) =>
    init?.method === "POST"
      ? pending.promise
      : Promise.resolve(Response.json(path.includes(C) ? second : first)),
  );
  render(<Discover />);
  await waitFor(() => expect(card()).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Далее →" }));
  expect(card()).toBeDisabled();
  expect(navigationUrl()).toBe(url());
  expect(screen.getAllByRole("status")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Далее →" })).toBeDisabled();
  await finish(pending, second);
  expect(navigationUrl()).toBe(url(C));
  expect(posts()).toHaveLength(1);
  expect(gets()).toHaveLength(1);
  expect(testRouter.push).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "← Назад" }));
  await waitFor(() => expect(card()).toBeEnabled());
  expect(navigationUrl()).toBe(url());
  expect(gets()).toHaveLength(2);
  act(back);
  await screen.findByRole("button", { name: /Second vacancy/ });
  expect(gets()).toHaveLength(3);
  act(forward);
  await waitFor(() => expect(card()).toBeEnabled());
  expect(gets()).toHaveLength(4);
  expect(posts()).toHaveLength(1);
});

it("does not create paused history; Continue uses the issued token and refresh recovers it with one GET", async () => {
  let navigation = first;
  let post = 0;
  fetcher.mockImplementation((_path: string, init?: RequestInit) =>
    Promise.resolve(
      Response.json(
        init?.method === "POST" ? (++post === 1 ? paused : second) : navigation,
      ),
    ),
  );
  const view = render(<Discover />);
  await waitFor(() => expect(card()).toBeEnabled());
  fireEvent.click(card());
  const selectedUrl = navigationUrl();
  fireEvent.click(screen.getByRole("button", { name: "Далее →" }));
  await screen.findByText("Пока не нашли новые удалённые вакансии.");
  expect(navigationUrl()).toBe(selectedUrl);
  expect(card()).toBeEnabled();
  expect(testRouter.push).not.toHaveBeenCalled();
  expect(posts()).toHaveLength(1);
  expect(gets()).toHaveLength(1);
  navigation = { ...first, next_cursor: C, next_action: "continue" };
  view.unmount();
  render(<Discover />);
  await screen.findByRole("button", { name: "Продолжить поиск" });
  expect(gets()).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Продолжить поиск" }));
  await screen.findByRole("button", { name: /Second vacancy/ });
  expect(JSON.parse(posts()[1][1].body)).toEqual({ cursor: C });
  expect(navigationUrl()).toBe(url(C));
  expect(testRouter.push).toHaveBeenCalledTimes(1);
  expect(gets()).toHaveLength(2);
  act(back);
  await screen.findByRole("button", { name: "Продолжить поиск" });
  expect(navigationUrl()).toBe(selectedUrl);
  expect(posts()).toHaveLength(2);
});

it("treats multiple paused metadata as one Continue and does not loop on another paused response", async () => {
  fetcher.mockImplementation((_path: string, init?: RequestInit) =>
    Promise.resolve(
      Response.json(
        init?.method === "POST"
          ? { ...paused, cursor: D, next_cursor: B }
          : { ...first, next_action: "continue", next_cursor: D },
      ),
    ),
  );
  render(<Discover />);
  await screen.findByRole("button", { name: "Продолжить поиск" });
  fireEvent.click(screen.getByRole("button", { name: "Продолжить поиск" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Продолжить поиск" }),
    ).toBeEnabled(),
  );
  expect(posts()).toHaveLength(1);
  expect(JSON.parse(posts()[0][1].body)).toEqual({ cursor: D });
  expect(navigationUrl()).toBe(url());
  expect(testRouter.push).not.toHaveBeenCalled();
});

it("keeps cards/selection on end and refresh remains terminal", async () => {
  let terminal = false;
  fetcher.mockImplementation((_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      terminal = true;
      return Promise.resolve(Response.json(end));
    }
    return Promise.resolve(
      Response.json(
        terminal ? { ...first, next_cursor: null, next_action: null } : first,
      ),
    );
  });
  const view = render(<Discover />);
  await waitFor(() => expect(card()).toBeEnabled());
  fireEvent.click(card());
  const selectedUrl = navigationUrl();
  fireEvent.click(screen.getByRole("button", { name: "Далее →" }));
  await screen.findByText("Больше удалённых вакансий не найдено.");
  expect(card()).toBeEnabled();
  expect(screen.getByLabelText("Описание вакансии")).toBeInTheDocument();
  expect(navigationUrl()).toBe(selectedUrl);
  expect(
    screen.queryByRole("button", { name: "Далее →" }),
  ).not.toBeInTheDocument();
  view.unmount();
  render(<Discover />);
  await screen.findByText("Больше удалённых вакансий не найдено.");
  expect(gets()).toHaveLength(2);
  expect(posts()).toHaveLength(1);
});

it.each([
  ["feed_busy", 409],
  ["feed_busy", 503],
  ["source_timeout", 504],
  ["feed_unavailable", 500],
] as const)(
  "preserves page and retries the same token after %s/%s without auto retry",
  async (code, status) => {
    fetcher.mockImplementation((_path: string, init?: RequestInit) =>
      Promise.resolve(
        init?.method === "POST"
          ? Response.json({ code }, { status })
          : Response.json(first),
      ),
    );
    render(<Discover />);
    await waitFor(() => expect(card()).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Далее →" }));
    await screen.findByText("Не удалось продолжить поиск.");
    expect(card()).toBeEnabled();
    expect(navigationUrl()).toBe(url());
    expect(posts()).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts().map(([, init]) => JSON.parse(init.body))).toEqual([
      { cursor: B },
      { cursor: B },
    ]);
    expect(gets()).toHaveLength(1);
    expect(testRouter.push).not.toHaveBeenCalled();
  },
);

it.each([404, 410])(
  "requires explicit restart for unavailable %s and keeps old cursor until success",
  async (status) => {
    const pending = deferred();
    fetcher.mockImplementation((_path: string, init?: RequestInit) =>
      init?.method === "POST"
        ? pending.promise
        : Promise.resolve(Response.json({ code: "feed_expired" }, { status })),
    );
    render(<Discover />);
    await screen.findByText("Эти результаты больше недоступны.");
    expect(posts()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Повторить поиск" }));
    expect(navigationUrl()).toBe(url());
    expect(JSON.parse(posts()[0][1].body)).toEqual({
      query: "Python",
      remote_only: true,
    });
    await finish(pending, second);
    expect(navigationUrl()).toBe(url(C));
    expect(gets()).toHaveLength(1);
  },
);

it("retries a failed initial start using retry_cursor instead of creating another chain", async () => {
  resetNavigation();
  let post = 0;
  fetcher.mockImplementation(() =>
    Promise.resolve(
      ++post === 1
        ? Response.json({ code: "feed_busy", retry_cursor: B }, { status: 409 })
        : Response.json({ ...first, cursor: B, next_cursor: C }),
    ),
  );
  render(<Discover />);
  submit();
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
  await waitFor(() => expect(card()).toBeEnabled());
  expect(posts().map(([, init]) => JSON.parse(init.body))).toEqual([
    { query: "Python", remote_only: true },
    { cursor: B },
  ]);
  expect(navigationUrl()).toBe(url(B));
  expect(gets()).toHaveLength(0);
});

it("ignores late Start A after Start B and never canonicalizes A", async () => {
  resetNavigation();
  const a = deferred(),
    b = deferred();
  fetcher.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
  render(<Discover />);
  submit("A");
  submit("B");
  await finish(b, { ...first, cursor: C });
  await finish(a, first);
  expect(navigationUrl()).toBe(url(C, "B"));
  expect(posts()).toHaveLength(2);
  expect(testRouter.replace).toHaveBeenCalledTimes(1);
  expect(gets()).toHaveLength(0);
});

it("ignores late Next after browser Back", async () => {
  resetNavigation(url(A, "earlier"));
  testRouter.push(url(A, "current"));
  const pending = deferred();
  fetcher.mockImplementation((_path: string, init?: RequestInit) =>
    init?.method === "POST"
      ? pending.promise
      : Promise.resolve(Response.json(first)),
  );
  render(<Discover />);
  await waitFor(() => expect(card()).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Далее →" }));
  act(back);
  await waitFor(() => expect(card()).toBeEnabled());
  await finish(pending, second);
  expect(navigationUrl()).toBe(url(A, "earlier"));
  expect(posts()).toHaveLength(1);
  expect(gets()).toHaveLength(2);
});

it("ignores late replay after a new query submit", async () => {
  const pending = deferred();
  fetcher
    .mockReturnValueOnce(pending.promise)
    .mockResolvedValue(Response.json(second));
  render(<Discover />);
  submit("new");
  await screen.findByRole("button", { name: /Second vacancy/ });
  await finish(pending, first);
  expect(navigationUrl()).toBe(url(C, "new"));
  expect(
    screen.queryByRole("button", { name: /Python developer.*Test company/ }),
  ).not.toBeInTheDocument();
  expect(gets()).toHaveLength(1);
  expect(posts()).toHaveLength(1);
});

it("switches off to legacy and ignores late Continue, then remote submit starts a fresh feed", async () => {
  const pending = deferred();
  fetcher.mockImplementation((path: string, init?: RequestInit) =>
    path.startsWith("/api/discover?")
      ? Promise.resolve(Response.json(page))
      : init?.method === "POST"
        ? pending.promise
        : Promise.resolve(Response.json({ ...first, next_action: "continue" })),
  );
  render(<Discover />);
  await screen.findByRole("button", { name: "Продолжить поиск" });
  fireEvent.click(screen.getByRole("button", { name: "Продолжить поиск" }));
  submit("normal", false);
  await screen.findByText("Страница 1");
  await finish(pending, second);
  expect(navigationUrl()).toBe("/discover?query=normal");
  fetcher.mockResolvedValue(Response.json(first));
  submit("remote", true);
  await waitFor(() => expect(navigationUrl()).toBe(url(A, "remote")));
  expect(JSON.parse(posts()[1][1].body)).toEqual({
    query: "remote",
    remote_only: true,
  });
});

it("Application return stores only visible cursor and replay restores fresh saved state/selection", async () => {
  resetNavigation(url(A, "Python", vacancyKey(vacancy)));
  fetcher.mockResolvedValue(
    Response.json({
      ...first,
      items: [
        {
          ...first.items[0],
          application_id: 42,
          application_status: "saved",
          already_saved_for_user: true,
        },
      ],
    }),
  );
  const view = render(<Discover />);
  const link = await screen.findByRole("link", {
    name: "Открыть сохранённую вакансию →",
  });
  const from = new URL(
    link.getAttribute("href")!,
    "http://test",
  ).searchParams.get("from");
  expect(from).toBe(navigationUrl());
  expect(from).not.toContain(B);
  view.unmount();
  fetcher.mockResolvedValue(
    Response.json({
      ...first,
      items: [
        {
          ...first.items[0],
          application_id: 42,
          application_status: "applied",
          already_saved_for_user: true,
        },
      ],
    }),
  );
  render(<Discover />);
  await screen.findByText(/Текущий статус:/);
  expect(
    screen.getByRole("link", { name: "Открыть сохранённую вакансию →" }),
  ).toBeInTheDocument();
  expect(gets()).toHaveLength(2);
  expect(posts()).toHaveLength(0);
});

it("delays slow-source copy 2800ms for start/Continue and has one live region", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  resetNavigation();
  const pending = deferred();
  fetcher.mockReturnValue(pending.promise);
  render(<Discover />);
  submit();
  await act(async () => vi.advanceTimersByTime(2799));
  expect(
    screen.queryByText("Источник отвечает медленнее обычного"),
  ).not.toBeInTheDocument();
  await act(async () => vi.advanceTimersByTime(1));
  expect(screen.getByRole("status")).toHaveTextContent(
    "Источник отвечает медленнее обычного",
  );
  await finish(pending, { ...first, next_action: "continue" });
  const advance = deferred();
  fetcher.mockReturnValue(advance.promise);
  fireEvent.click(screen.getByRole("button", { name: "Продолжить поиск" }));
  expect(card()).toBeDisabled();
  await act(async () => vi.advanceTimersByTime(2800));
  expect(screen.getAllByRole("status")).toHaveLength(1);
  expect(screen.getByRole("status")).toHaveTextContent(
    "Источник отвечает медленнее обычного",
  );
  expect(
    document.querySelector(".discover-pagination-feedback"),
  ).toHaveAttribute("aria-hidden", "true");
  await finish(advance, paused);
  expect(card()).toBeEnabled();
  expect(
    screen.queryByText("Источник отвечает медленнее обычного"),
  ).not.toBeInTheDocument();
  expect(posts()).toHaveLength(2);
});
it("keeps replay copy neutral even past the threshold and aborts a real unmount", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  const pending = deferred();
  fetcher.mockReturnValue(pending.promise);
  const view = render(
    <StrictMode>
      <Discover />
    </StrictMode>,
  );
  expect(gets()).toHaveLength(1);
  expect(screen.getByRole("status")).toHaveTextContent("Загружаем результаты");
  expect(
    screen.getByRole("button", { name: "Найти вакансии" }),
  ).toHaveTextContent("Загружаем");
  await act(async () => vi.advanceTimersByTime(3000));
  expect(
    screen.queryByText("Источник отвечает медленнее обычного"),
  ).not.toBeInTheDocument();
  const signal = gets()[0][1].signal;
  view.unmount();
  await act(async () => {});
  expect(signal.aborted).toBe(true);
});
it("does not treat an unrelated 404 as expired or start a new chain", async () => {
  fetcher.mockResolvedValue(
    Response.json({ code: "feed_unavailable" }, { status: 404 }),
  );
  render(<Discover />);
  await screen.findByRole("alert");
  expect(
    screen.queryByText("Эти результаты больше недоступны."),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
  await waitFor(() => expect(gets()).toHaveLength(2));
  expect(posts()).toHaveLength(0);
});

it("does not duplicate replay when Next publishes a popped URL before our popstate handler", async () => {
  render(<Discover />);
  await waitFor(() => expect(card()).toBeEnabled());
  act(() => testRouter.replace(url(C)));
  await waitFor(() => expect(gets()).toHaveLength(2));
  act(() => window.dispatchEvent(new PopStateEvent("popstate")));
  await act(async () => {});
  expect(gets()).toHaveLength(2);
  expect(posts()).toHaveLength(0);
});

it.each(
  (["paused", "end"] as const).flatMap((outcome) =>
    (
      [
        "save-first",
        "advance-first",
        "save-fails-first",
        "save-fails-last",
      ] as const
    ).map((ordering) => ({ outcome, ordering })),
  ),
)(
  "preserves current Save/selection on $outcome ($ordering) without replay",
  async ({ outcome, ordering }) => {
    const advance = deferred(),
      saving = deferred();
    const other = {
      ...first.items[0],
      external_id: "other",
      title: "Other vacancy",
    };
    fetcher.mockImplementation((path: string, init?: RequestInit) =>
      path === "/api/discover/save"
        ? saving.promise
        : init?.method === "POST"
          ? advance.promise
          : Promise.resolve(
              Response.json({
                ...first,
                items: [first.items[0], other],
                returned_count: 2,
              }),
            ),
    );
    render(<Discover />);
    await waitFor(() => expect(card()).toBeEnabled());
    fireEvent.click(card());
    const selectedUrl = navigationUrl();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить вакансию" }));
    fireEvent.click(screen.getByRole("button", { name: "Далее →" }));
    const failed = ordering.startsWith("save-fails");
    const saveFirst =
      ordering === "save-first" || ordering === "save-fails-first";
    const finishSave = () =>
      act(async () =>
        saving.resolve(
          failed
            ? Response.json({ code: "ambiguous_save" }, { status: 503 })
            : Response.json(saved),
        ),
      );
    if (saveFirst) await finishSave();
    await finish(advance, outcome === "paused" ? paused : end);
    if (!saveFirst) await finishSave();
    expect(navigationUrl()).toBe(selectedUrl);
    expect(card()).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Описание вакансии")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Other vacancy/ }),
    ).not.toHaveTextContent("Собеседование");
    if (failed) {
      expect(
        screen.getByRole("button", { name: "Сохранить вакансию" }),
      ).toBeEnabled();
      expect(
        screen.queryByRole("link", { name: "Открыть сохранённую вакансию →" }),
      ).not.toBeInTheDocument();
    } else {
      expect(
        screen.queryByRole("button", { name: "Сохранить вакансию" }),
      ).not.toBeInTheDocument();
      expect(screen.getByText(/Текущий статус:/)).toHaveTextContent(
        "Собеседование",
      );
      expect(
        screen.getByRole("link", { name: "Открыть сохранённую вакансию →" }),
      ).toHaveAttribute(
        "href",
        `/applications/42?from=${encodeURIComponent(selectedUrl)}`,
      );
    }
    if (outcome === "paused") {
      expect(
        screen.getByRole("button", { name: "Продолжить поиск" }),
      ).toBeEnabled();
      fetcher.mockResolvedValue(Response.json(end));
      fireEvent.click(screen.getByRole("button", { name: "Продолжить поиск" }));
      await screen.findByText("Больше удалённых вакансий не найдено.");
      expect(JSON.parse(posts()[1][1].body)).toEqual({ cursor: C });
    } else {
      expect(
        screen.queryByRole("button", { name: "Далее →" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Продолжить поиск" }),
      ).not.toBeInTheDocument();
    }
    expect(posts()).toHaveLength(outcome === "paused" ? 2 : 1);
    expect(gets()).toHaveLength(1);
    expect(
      fetcher.mock.calls.filter(([path]) => path === "/api/discover/save"),
    ).toHaveLength(1);
    expect(testRouter.push).not.toHaveBeenCalled();
    expect(navigationUrl()).toBe(selectedUrl);
  },
);

it.each(["paused", "end"] as const)(
  "drops stale %s metadata after navigating to a different cursor",
  async (outcome) => {
    const advance = deferred();
    fetcher.mockImplementation((path: string, init?: RequestInit) =>
      init?.method === "POST"
        ? advance.promise
        : Promise.resolve(Response.json(path.includes(C) ? second : first)),
    );
    render(<Discover />);
    await waitFor(() => expect(card()).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Далее →" }));
    act(() => testRouter.push(url(C)));
    await screen.findByRole("button", { name: /Second vacancy/ });
    await finish(advance, outcome === "paused" ? paused : end);
    expect(navigationUrl()).toBe(url(C));
    expect(
      screen.getByRole("button", { name: /Second vacancy/ }),
    ).toBeEnabled();
    expect(screen.getByRole("button", { name: "Далее →" })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "Продолжить поиск" }),
    ).not.toBeInTheDocument();
    expect(gets()).toHaveLength(2);
    expect(posts()).toHaveLength(1);
  },
);

it.each(["next", "continue"] as const)(
  "restores focused %s to the terminal status without changing the URL",
  async (action) => {
    const advance = deferred();
    fetcher.mockImplementation((_path: string, init?: RequestInit) =>
      init?.method === "POST"
        ? advance.promise
        : Promise.resolve(Response.json({ ...first, next_action: action })),
    );
    render(<Discover />);
    const button = await screen.findByRole("button", {
      name: action === "next" ? "Далее →" : "Продолжить поиск",
    });
    button.focus();
    fireEvent.click(button);
    await finish(advance, end);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Больше удалённых вакансий не найдено.");
    expect(status).toHaveAttribute("tabindex", "-1");
    expect(status).toHaveFocus();
    expect(button).not.toBeInTheDocument();
    expect(navigationUrl()).toBe(url());
    expect(posts()).toHaveLength(1);
    expect(gets()).toHaveLength(1);
  },
);

it.each([false, true])(
  "does not steal terminal focus after the user moved elsewhere (then blurred=%s)",
  async (blurred) => {
    const advance = deferred();
    fetcher.mockImplementation((_path: string, init?: RequestInit) =>
      init?.method === "POST"
        ? advance.promise
        : Promise.resolve(Response.json(first)),
    );
    render(<Discover />);
    const button = await screen.findByRole("button", { name: "Далее →" });
    button.focus();
    fireEvent.click(button);
    const query = screen.getByLabelText("Должность или ключевые слова");
    query.focus();
    if (blurred) query.blur();
    await finish(advance, end);
    expect(blurred ? document.body : query).toHaveFocus();
    expect(screen.getByRole("status")).not.toHaveFocus();
  },
);

it("keeps focus on the existing forward control after paused, then preserves page-success focus", async () => {
  const advance = deferred();
  fetcher.mockImplementation((_path: string, init?: RequestInit) =>
    init?.method === "POST"
      ? advance.promise
      : Promise.resolve(Response.json(first)),
  );
  render(<Discover />);
  const button = await screen.findByRole("button", { name: "Далее →" });
  button.focus();
  fireEvent.click(button);
  await finish(advance, paused);
  expect(screen.getByRole("button", { name: "Продолжить поиск" })).toBe(button);
  expect(button).toHaveFocus();
  fetcher.mockResolvedValue(Response.json(second));
  fireEvent.click(button);
  await screen.findByRole("button", { name: /Second vacancy/ });
  expect(document.querySelector(".results-list")).toHaveFocus();
  expect(posts()).toHaveLength(2);
  expect(gets()).toHaveLength(1);
});

it("does not move focus for a passive terminal replay or an unfocused forward action", async () => {
  fetcher.mockResolvedValue(
    Response.json({ ...first, next_cursor: null, next_action: null }),
  );
  const view = render(<Discover />);
  await waitFor(() => expect(card()).toBeEnabled());
  expect(screen.getByRole("status")).not.toHaveFocus();
  view.unmount();
  fetcher.mockImplementation((_path: string, init?: RequestInit) =>
    Promise.resolve(Response.json(init?.method === "POST" ? end : first)),
  );
  render(<Discover />);
  await waitFor(() => expect(card()).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Далее →" }));
  await screen.findByText("Больше удалённых вакансий не найдено.");
  expect(screen.getByRole("status")).not.toHaveFocus();
});

function blurDisabledForward(button: HTMLButtonElement) {
  expect(button).toBeDisabled();
  // jsdom does not apply the browser's disabled-element focus fixup, and its
  // blur() is a no-op on disabled elements. Briefly enable only to perform the
  // real DOM blur, then restore the pending disabled state before completion.
  button.disabled = false;
  button.blur();
  button.disabled = true;
  expect(document.activeElement).toBe(document.body);
}

it.each(["next", "continue"] as const)(
  "restores %s → paused focus after browser blur and supports another Continue",
  async (action) => {
    const advances = [deferred(), deferred()];
    let post = 0;
    fetcher.mockImplementation((_path: string, init?: RequestInit) =>
      init?.method === "POST"
        ? advances[post++].promise
        : Promise.resolve(Response.json({ ...first, next_action: action })),
    );
    render(<Discover />);
    let button = (await screen.findByRole("button", {
      name: action === "next" ? "Далее →" : "Продолжить поиск",
    })) as HTMLButtonElement;
    button.focus();
    for (let index = 0; index < advances.length; index++) {
      fireEvent.click(button);
      blurDisabledForward(button);
      expect(screen.getAllByRole("status")).toHaveLength(1);
      await finish(advances[index], {
        ...paused,
        cursor: index === 0 ? B : C,
        next_cursor: index === 0 ? C : D,
      });
      button = screen.getByRole("button", {
        name: "Продолжить поиск",
      }) as HTMLButtonElement;
      expect(document.activeElement).toBe(button);
      expect(button).toBeEnabled();
      expect(button.tabIndex).toBe(0);
      expect(screen.getAllByRole("status")).toHaveLength(1);
      expect(navigationUrl()).toBe(url());
      expect(posts()).toHaveLength(index + 1);
      expect(gets()).toHaveLength(1);
    }
    expect(posts().map(([, init]) => JSON.parse(init.body))).toEqual([
      { cursor: B },
      { cursor: C },
    ]);
    expect(testRouter.push).not.toHaveBeenCalled();
  },
);

it.each(
  (["next", "continue"] as const).flatMap((action) =>
    (["other-control", "other-then-blur", "outside-click"] as const).map(
      (move) => ({ action, move }),
    ),
  ),
)(
  "respects focus changes during $action → paused ($move)",
  async ({ action, move }) => {
    const advance = deferred();
    fetcher.mockImplementation((_path: string, init?: RequestInit) =>
      init?.method === "POST"
        ? advance.promise
        : Promise.resolve(Response.json({ ...first, next_action: action })),
    );
    render(<Discover />);
    const button = (await screen.findByRole("button", {
      name: action === "next" ? "Далее →" : "Продолжить поиск",
    })) as HTMLButtonElement;
    button.focus();
    fireEvent.click(button);
    blurDisabledForward(button);
    const query = screen.getByLabelText("Должность или ключевые слова");
    if (move === "outside-click")
      fireEvent.pointerDown(
        screen.getByRole("heading", { name: "Поиск вакансий" }),
      );
    else {
      query.focus();
      if (move === "other-then-blur") query.blur();
    }
    const intendedFocus = document.activeElement;
    await finish(advance, paused);
    expect(document.activeElement).toBe(intendedFocus);
    expect(
      screen.getByRole("button", { name: "Продолжить поиск" }),
    ).not.toHaveFocus();
    expect(navigationUrl()).toBe(url());
    expect(posts()).toHaveLength(1);
    expect(gets()).toHaveLength(1);
  },
);

it("does not steal focus on passive Continue replay or after a failed Continue", async () => {
  const advance = deferred();
  fetcher.mockImplementation((_path: string, init?: RequestInit) =>
    init?.method === "POST"
      ? advance.promise
      : Promise.resolve(Response.json({ ...first, next_action: "continue" })),
  );
  render(<Discover />);
  const button = (await screen.findByRole("button", {
    name: "Продолжить поиск",
  })) as HTMLButtonElement;
  expect(button).not.toHaveFocus();
  button.focus();
  fireEvent.click(button);
  blurDisabledForward(button);
  await act(async () =>
    advance.resolve(Response.json({ code: "feed_busy" }, { status: 409 })),
  );
  expect(document.activeElement).toBe(document.body);
  expect(button).not.toHaveFocus();
  expect(posts()).toHaveLength(1);
  expect(gets()).toHaveLength(1);
});
