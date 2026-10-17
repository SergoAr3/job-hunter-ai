import { StrictMode } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Discover } from "../components/discover";
import { page, vacancy } from "./fixtures";
import type { DiscoverPage } from "../lib/contracts";
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

function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
const fetcher = vi.fn();
const slowText = "Источник отвечает медленнее обычного";
const card = () =>
  screen.getByRole("button", { name: /Python developer.*Test company/ });
const nextPage = {
  ...page,
  items: [{ ...vacancy, external_id: "page-2", title: "Second page vacancy" }],
  offset: 10,
  source_total: 20,
  next_offset: null,
};
const firstPage = { ...page, source_total: 20, next_offset: 10 };
async function finish(
  pending: ReturnType<typeof deferred>,
  value: DiscoverPage = firstPage,
) {
  await act(async () => {
    pending.resolve(Response.json(value));
  });
}
async function tick(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
  });
}
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  resetNavigation("/discover?query=Python");
  fetcher.mockReset();
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("shows initial skeletons, delays slow feedback, and clears the timer on success even in StrictMode", async () => {
  const pending = deferred();
  fetcher.mockReturnValue(pending.promise);
  const view = render(
    <StrictMode>
      <Discover />
    </StrictMode>,
  );
  expect(screen.getByRole("status")).toHaveTextContent(
    "Ищем подходящие вакансии…",
  );
  expect(screen.getByRole("status")).toHaveTextContent(
    "Проверяем источник и обновляем результаты",
  );
  expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
  expect(screen.getByRole("status")).toHaveAttribute("aria-atomic", "true");
  expect(
    view.container.querySelector(".discover-search-motion"),
  ).toHaveAttribute("aria-hidden", "true");
  expect(view.container.querySelector(".discover-skeletons")).toHaveAttribute(
    "aria-hidden",
    "true",
  );
  expect(
    view.container.querySelectorAll(".discover-skeleton-card"),
  ).toHaveLength(4);
  await tick(2799);
  expect(screen.queryByText(slowText)).not.toBeInTheDocument();
  await tick(1);
  expect(screen.getByRole("status")).toHaveTextContent(slowText);
  expect(screen.getByRole("status")).toHaveTextContent(
    "Продолжаем ждать актуальные вакансии",
  );
  expect(fetcher).toHaveBeenCalledTimes(1);
  await finish(pending);
  expect(card()).toBeEnabled();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(view.container.querySelector(".discover-skeletons")).toBeNull();
  expect(vi.getTimerCount()).toBe(0);
});

it("never reaches slow feedback for a fast response and does not fire it later", async () => {
  const pending = deferred();
  fetcher.mockReturnValue(pending.promise);
  render(<Discover />);
  await tick(50);
  await finish(pending);
  await tick(5000);
  expect(card()).toBeEnabled();
  expect(screen.queryByText(slowText)).not.toBeInTheDocument();
  expect(vi.getTimerCount()).toBe(0);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("retains and labels old pagination results, disables actions, then replaces them with the new page", async () => {
  const first = deferred();
  const second = deferred();
  fetcher
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise);
  render(<Discover />);
  await finish(first);
  fireEvent.click(card());
  expect(
    screen.getByRole("button", { name: "Сохранить вакансию" }),
  ).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Далее →" }));
  expect(navigationUrl()).toBe("/discover?query=Python&offset=10");
  expect(card()).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent(
    "Загружаем следующую страницу…",
  );
  expect(
    screen.getByText("Предыдущие результаты · страница 1"),
  ).toBeInTheDocument();
  const region = screen.getByRole("region", { name: "Результаты поиска" });
  expect(region).toHaveClass("is-stale");
  expect(region).toHaveAttribute("aria-busy", "true");
  expect(region).toHaveAccessibleDescription(
    expect.stringContaining("Пока показаны предыдущие результаты"),
  );
  for (const button of within(region).getAllByRole("button"))
    expect(button).toBeDisabled();
  fireEvent.click(card());
  fireEvent.click(screen.getByRole("button", { name: "Далее →" }));
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(screen.queryByLabelText("Описание вакансии")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Сохранить вакансию" }),
  ).not.toBeInTheDocument();
  await tick(2800);
  expect(screen.getByRole("status")).toHaveTextContent(
    "Продолжаем загружать следующую страницу",
  );
  await finish(second, nextPage);
  expect(screen.queryByText("Python developer")).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /Second page vacancy/ }),
  ).toBeEnabled();
  expect(screen.getByText("Страница 2")).toBeInTheDocument();
  expect(region).toHaveAttribute("aria-busy", "false");
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

it("keeps feedback inside bottom pagination for a long stale page, sharing one timer and one live announcement", async () => {
  const first = deferred();
  const second = deferred();
  fetcher
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise);
  const view = render(
    <StrictMode>
      <Discover />
    </StrictMode>,
  );
  await finish(first, {
    ...firstPage,
    returned_count: 10,
    items: Array.from({ length: 10 }, (_, index) => ({
      ...vacancy,
      external_id: `long-page-${index}`,
      description: "Подробное описание предыдущей вакансии. ".repeat(30),
    })),
  });
  expect(view.container.querySelectorAll(".vacancy-card")).toHaveLength(10);
  const nav = screen.getByRole("navigation", { name: "Страницы результатов" });
  fireEvent.click(within(nav).getByRole("button", { name: "Далее →" }));
  const feedback = nav.querySelector(".discover-pagination-feedback")!;
  expect(feedback).toHaveTextContent("Загружаем следующую страницу…");
  expect(feedback.parentElement).toHaveClass("discover-pagination-position");
  expect(feedback).toHaveAttribute("aria-hidden", "true");
  expect(feedback).not.toHaveAttribute("role");
  expect(nav.querySelector("[aria-live]")).toBeNull();
  expect(screen.getAllByRole("status")).toHaveLength(1);
  expect(vi.getTimerCount()).toBe(1);
  for (const button of within(nav).getAllByRole("button"))
    expect(button).toBeDisabled();
  await tick(2799);
  expect(feedback).not.toHaveTextContent(slowText);
  await tick(1);
  expect(feedback).toHaveTextContent(slowText);
  expect(feedback).toHaveTextContent("Продолжаем загружать следующую страницу");
  expect(screen.getAllByRole("status")).toHaveLength(1);
  expect(screen.getByRole("status")).toHaveTextContent(slowText);
  expect(fetcher).toHaveBeenCalledTimes(2);
  await finish(second, nextPage);
  expect(nav.querySelector(".discover-pagination-feedback")).toBeNull();
  expect(screen.getByText("Страница 2")).toBeInTheDocument();
  expect(vi.getTimerCount()).toBe(0);
});

it("removes Application links during reload and restores fresh state with the exact return context", async () => {
  const identity = JSON.stringify([
    vacancy.source,
    vacancy.source_scope,
    vacancy.external_id,
  ]);
  const url = `/discover?${new URLSearchParams({ query: "Python", selected: identity })}`;
  resetNavigation(url);
  const first = deferred();
  const refresh = deferred();
  fetcher
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(refresh.promise);
  render(<Discover />);
  const savedPage = {
    ...firstPage,
    items: [
      {
        ...vacancy,
        already_saved_for_user: true,
        application_id: 42,
        application_status: "applied" as const,
      },
    ],
  };
  await finish(first, savedPage);
  expect(
    screen.getByRole("link", { name: /Открыть сохранённую/ }),
  ).toBeInTheDocument();
  const restore = new Event("pageshow");
  Object.defineProperty(restore, "persisted", { value: true });
  fireEvent(window, restore);
  expect(screen.getByRole("status")).toHaveTextContent("Обновляем результаты…");
  expect(card()).toBeDisabled();
  expect(
    screen.queryByRole("link", { name: /Открыть сохранённую/ }),
  ).not.toBeInTheDocument();
  await finish(refresh, savedPage);
  expect(
    screen.getByRole("link", { name: /Открыть сохранённую/ }),
  ).toHaveAttribute("href", `/applications/42?from=${encodeURIComponent(url)}`);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("clears stale results and slow feedback on error; Retry restarts the threshold without automatic requests", async () => {
  const first = deferred();
  const failed = deferred();
  const retry = deferred();
  fetcher
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(failed.promise)
    .mockReturnValueOnce(retry.promise);
  render(<Discover />);
  await finish(first);
  fireEvent.click(screen.getByRole("button", { name: "Далее →" }));
  await tick(2800);
  expect(
    document.querySelector(".discover-pagination-feedback"),
  ).toHaveTextContent(slowText);
  await act(async () => {
    failed.resolve(Response.json({ code: "source_timeout" }, { status: 504 }));
  });
  expect(screen.getByRole("alert")).toBeInTheDocument();
  expect(document.querySelector(".discover-pagination-feedback")).toBeNull();
  expect(screen.queryByText(slowText)).not.toBeInTheDocument();
  expect(
    screen.queryByRole("region", { name: "Результаты поиска" }),
  ).not.toBeInTheDocument();
  expect(vi.getTimerCount()).toBe(0);
  await tick(5000);
  expect(fetcher).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Повторить поиск" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent(
    "Ищем подходящие вакансии…",
  );
  await tick(2799);
  expect(screen.queryByText(slowText)).not.toBeInTheDocument();
  await tick(1);
  expect(screen.getByText(slowText)).toBeInTheDocument();
  await finish(retry, nextPage);
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(vi.getTimerCount()).toBe(0);
  expect(fetcher).toHaveBeenCalledTimes(3);
});

it("resets slow timing on Back/Forward and keeps old results disabled until the authoritative response", async () => {
  const first = deferred();
  const second = deferred();
  const backPage = deferred();
  const forwardPage = deferred();
  fetcher
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise)
    .mockReturnValueOnce(backPage.promise)
    .mockReturnValueOnce(forwardPage.promise);
  render(<Discover />);
  await finish(first);
  fireEvent.click(screen.getByRole("button", { name: "Далее →" }));
  await tick(2800);
  await finish(second, nextPage);
  act(back);
  expect(screen.getByRole("status")).toHaveTextContent(
    "Загружаем предыдущую страницу…",
  );
  expect(screen.queryByText(slowText)).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /Second page vacancy/ }),
  ).toBeDisabled();
  await tick(50);
  await finish(backPage);
  expect(navigationUrl()).toBe("/discover?query=Python");
  expect(card()).toBeEnabled();
  act(forward);
  expect(screen.getByRole("status")).toHaveTextContent(
    "Загружаем следующую страницу…",
  );
  await tick(50);
  await finish(forwardPage, nextPage);
  expect(navigationUrl()).toBe("/discover?query=Python&offset=10");
  await tick(3000);
  expect(screen.queryByText(slowText)).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(4);
});

it("a new search activation gets its own timer and late old responses cannot clear it", async () => {
  const first = deferred();
  const second = deferred();
  fetcher
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise);
  render(<Discover />);
  await tick(2800);
  fireEvent.change(screen.getByLabelText("Должность или ключевые слова"), {
    target: { value: "QA" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Найти вакансии" }));
  expect(screen.queryByText(slowText)).not.toBeInTheDocument();
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
  await finish(first);
  expect(screen.queryByText("Python developer")).not.toBeInTheDocument();
  await tick(2799);
  expect(screen.queryByText(slowText)).not.toBeInTheDocument();
  await tick(1);
  expect(screen.getByText(slowText)).toBeInTheDocument();
  await finish(second);
  expect(card()).toBeEnabled();
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("a queued callback from a cleaned timer cannot clear the next activation's slow state", async () => {
  const pending = deferred();
  fetcher.mockReturnValue(pending.promise);
  const scheduled = vi.spyOn(window, "setTimeout");
  render(<Discover />);
  const oldCallback = scheduled.mock.calls[0][0] as () => void;
  await tick(2000);
  fireEvent.change(screen.getByLabelText("Должность или ключевые слова"), {
    target: { value: "QA" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Найти вакансии" }));
  await tick(2800);
  expect(screen.getByRole("status")).toHaveTextContent(slowText);
  act(oldCallback);
  expect(screen.getByRole("status")).toHaveTextContent(slowText);
  expect(fetcher).toHaveBeenCalledTimes(2);
  await finish(pending);
});

it("cleans the timer on navigation to an empty search and ignores the old response", async () => {
  const pending = deferred();
  fetcher.mockReturnValue(pending.promise);
  render(<Discover />);
  await tick(2000);
  act(() => testRouter.push("/discover"));
  expect(vi.getTimerCount()).toBe(0);
  await finish(pending);
  await tick(5000);
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(screen.queryByText("Python developer")).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("cleans the timer and aborts on unmount, including a late response", async () => {
  const pending = deferred();
  fetcher.mockReturnValue(pending.promise);
  const view = render(<Discover />);
  await tick(2000);
  view.unmount();
  await act(async () => {});
  expect(vi.getTimerCount()).toBe(0);
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
  await finish(pending);
  await tick(5000);
  expect(vi.getTimerCount()).toBe(0);
});

it.each([
  {
    initial: "/discover?query=Python&offset=20",
    next: "/discover?query=QA",
    copy: "Обновляем результаты…",
  },
  {
    initial: "/discover?query=Python&remote_only=true&offset=20",
    next: "/discover?query=Python",
    copy: "Обновляем результаты…",
  },
  {
    initial: "/discover?query=Python&offset=20",
    next: "/discover?query=Python&offset=10",
    copy: "Загружаем предыдущую страницу…",
  },
  {
    initial: "/discover?query=Python&offset=20",
    next: "/discover?query=Python&offset=30",
    copy: "Загружаем следующую страницу…",
  },
])(
  "classifies legacy loading by query/filter identity: $initial → $next",
  async ({ initial, next, copy }) => {
    resetNavigation(initial);
    const first = deferred(),
      pending = deferred();
    fetcher.mockReturnValueOnce(first.promise).mockReturnValue(pending.promise);
    render(<Discover />);
    await finish(first, { ...firstPage, offset: 20, next_offset: 30 });
    if (next.includes("offset")) {
      fireEvent.click(
        screen.getByRole("button", {
          name: next.endsWith("10") ? "← Назад" : "Далее →",
        }),
      );
    } else {
      fireEvent.change(screen.getByLabelText("Должность или ключевые слова"), {
        target: { value: next.includes("QA") ? "QA" : "Python" },
      });
      const remote = screen.getByLabelText(
        "Только удалённая работа",
      ) as HTMLInputElement;
      if (remote.checked) fireEvent.click(remote);
      fireEvent.click(screen.getByRole("button", { name: "Найти вакансии" }));
    }
    expect(navigationUrl()).toBe(next);
    expect(screen.getByRole("status")).toHaveTextContent(copy);
    expect(card()).toBeDisabled();
    expect(
      document.querySelector(".discover-pagination-feedback"),
    ).toHaveTextContent(copy);
    await tick(2799);
    expect(screen.queryByText(slowText)).not.toBeInTheDocument();
    await tick(1);
    expect(screen.getByRole("status")).toHaveTextContent(slowText);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(
      fetcher.mock.calls.every(([path]) =>
        String(path).startsWith("/api/discover?"),
      ),
    ).toBe(true);
    await finish(pending);
  },
);
