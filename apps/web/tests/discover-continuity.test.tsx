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
import { page, vacancy } from "./fixtures";
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
const identity = JSON.stringify([
  vacancy.source,
  vacancy.source_scope,
  vacancy.external_id,
]);
const context = (
  query = "Python",
  remote = false,
  offset = 0,
  selected: string | null = null,
) => {
  const params = new URLSearchParams({ query });
  if (remote) params.set("remote_only", "true");
  if (offset) params.set("offset", String(offset));
  if (selected) params.set("selected", selected);
  return `/discover?${params}`;
};
const searchResponse = (url: string, items = [vacancy]) => {
  const offset = Number(new URL(url, "http://test").searchParams.get("offset"));
  return Response.json({
    ...page,
    items,
    offset,
    limit: 10,
    next_offset: offset + 10,
  });
};
const fetcher = vi.fn();
const originalScroll = Object.getOwnPropertyDescriptor(
  HTMLElement.prototype,
  "scrollIntoView",
);
const searchCalls = () =>
  fetcher.mock.calls.filter(([url]) =>
    String(url).startsWith("/api/discover?"),
  );
beforeEach(() => {
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 0;
  });
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  resetNavigation();
  fetcher
    .mockReset()
    .mockImplementation((url: string) =>
      Promise.resolve(
        url === "/api/profile"
          ? Response.json({ target_roles: ["Backend Developer", "QA"] })
          : searchResponse(url),
      ),
    );
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => {
  vi.unstubAllGlobals();
  if (originalScroll)
    Object.defineProperty(
      HTMLElement.prototype,
      "scrollIntoView",
      originalScroll,
    );
  else Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
});
function submit(query: string) {
  fireEvent.change(screen.getByLabelText("Должность или ключевые слова"), {
    target: { value: query },
  });
  fireEvent.click(screen.getByRole("button", { name: "Найти вакансии" }));
}
async function resultCard() {
  return waitFor(() => {
    const card = screen.getByRole("button", {
      name: /Python developer.*Test company/,
    });
    expect(card).toBeEnabled();
    return card;
  });
}

it("uses profile roles only as editable draft suggestions; submit alone confirms URL", async () => {
  render(<Discover />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Backend Developer" }),
  );
  expect(screen.getByLabelText("Должность или ключевые слова")).toHaveValue(
    "Backend Developer",
  );
  expect(searchCalls()).toHaveLength(0);
  expect(navigationUrl()).toBe("/discover");
  fireEvent.change(screen.getByLabelText("Должность или ключевые слова"), {
    target: { value: "  Python  " },
  });
  fireEvent.click(screen.getByLabelText("Только удалённая работа"));
  expect(navigationUrl()).toBe("/discover");
  fireEvent.click(screen.getByRole("button", { name: "Найти вакансии" }));
  await resultCard();
  expect(navigationUrl()).toBe(context("Python", true));
  expect(searchCalls()).toHaveLength(1);
  expect(searchCalls()[0][0]).toBe(
    "/api/discover?query=Python&remote_only=true&offset=0",
  );
  submit("draft submitted");
  await waitFor(() => expect(searchCalls()).toHaveLength(2));
  fireEvent.change(screen.getByLabelText("Должность или ключевые слова"), {
    target: { value: "unsubmitted" },
  });
  fireEvent.click(screen.getByLabelText("Только удалённая работа"));
  expect(navigationUrl()).toBe(context("draft submitted", true));
  expect(searchCalls()).toHaveLength(2);
  expect(await resultCard()).toBeInTheDocument();
});

it("loads a deep link once even under StrictMode and selection does not refetch", async () => {
  resetNavigation(context("Python", true, 20, identity));
  render(
    <StrictMode>
      <Discover />
    </StrictMode>,
  );
  await screen.findByLabelText("Описание вакансии");
  expect(screen.getByLabelText("Должность или ключевые слова")).toHaveValue(
    "Python",
  );
  expect(screen.getByLabelText("Только удалённая работа")).toBeChecked();
  expect(screen.getByText("Страница 3")).toBeInTheDocument();
  expect(searchCalls()).toHaveLength(1);
  expect(searchCalls()[0][0]).toContain("offset=20");
  fireEvent.click(screen.getByRole("button", { name: "← К результатам" }));
  expect(navigationUrl()).toBe(context("Python", true, 20));
  expect(await resultCard()).toHaveFocus();
  fireEvent.click(await resultCard());
  expect(navigationUrl()).toBe(context("Python", true, 20, identity));
  expect(searchCalls()).toHaveLength(1);
  expect(
    fetcher.mock.calls.filter(([url]) => url === "/api/profile"),
  ).toHaveLength(1);
});

it("Back and Forward restore a changed remote filter, not its unsubmitted draft", async () => {
  resetNavigation(context("Python", true));
  render(<Discover />);
  await resultCard();
  fireEvent.click(screen.getByLabelText("Только удалённая работа"));
  fireEvent.click(screen.getByRole("button", { name: "Найти вакансии" }));
  await waitFor(() => expect(searchCalls()).toHaveLength(2));
  await resultCard();
  expect(navigationUrl()).toBe(context());
  act(back);
  await resultCard();
  expect(screen.getByLabelText("Только удалённая работа")).toBeChecked();
  expect(searchCalls()).toHaveLength(3);
  act(forward);
  await resultCard();
  expect(screen.getByLabelText("Только удалённая работа")).not.toBeChecked();
  expect(searchCalls()).toHaveLength(4);
});

it("paginates confirmed params, resets offset on submit, and restores Back/Forward controls and results", async () => {
  resetNavigation(context("Python", true, 10));
  render(<Discover />);
  await resultCard();
  fireEvent.click(screen.getByRole("button", { name: "Далее →" }));
  await screen.findByText("Страница 3");
  expect(navigationUrl()).toBe(context("Python", true, 20));
  submit("QA");
  await screen.findByText("Страница 1");
  expect(navigationUrl()).toBe(context("QA", true));
  act(back);
  await screen.findByText("Страница 3");
  expect(screen.getByLabelText("Должность или ключевые слова")).toHaveValue(
    "Python",
  );
  expect(screen.getByLabelText("Только удалённая работа")).toBeChecked();
  act(back);
  await screen.findByText("Страница 2");
  act(forward);
  await screen.findByText("Страница 3");
  act(forward);
  await screen.findByText("Страница 1");
  expect(screen.getByLabelText("Должность или ключевые слова")).toHaveValue(
    "QA",
  );
  expect(searchCalls()).toHaveLength(7);
  expect(
    fetcher.mock.calls.filter(([url]) => url === "/api/profile"),
  ).toHaveLength(1);
});

it("refresh/return refetches authoritative Application state and retains exact Discover return context", async () => {
  resetNavigation(context("Python", true, 10, identity));
  let status = "applied";
  fetcher.mockImplementation((url: string) =>
    Promise.resolve(
      url === "/api/profile"
        ? Response.json({ target_roles: [] })
        : searchResponse(url, [
            {
              ...vacancy,
              already_saved_for_user: true,
              application_id: 42,
              application_status: status,
            } as typeof vacancy,
          ]),
    ),
  );
  const first = render(<Discover />);
  expect(
    await screen.findByText("Текущий статус: Отклик отправлен"),
  ).toBeInTheDocument();
  const href = screen
    .getByRole("link", { name: /Открыть сохранённую/ })
    .getAttribute("href")!;
  expect(href).toBe(
    `/applications/42?from=${encodeURIComponent(navigationUrl())}`,
  );
  const returnUrl = new URL(href, "http://test").searchParams.get("from")!;
  first.unmount();
  status = "offer";
  resetNavigation(returnUrl);
  render(<Discover />);
  expect(await screen.findByText("Текущий статус: Оффер")).toBeInTheDocument();
  expect(
    screen.queryByText("Текущий статус: Отклик отправлен"),
  ).not.toBeInTheDocument();
  expect(searchCalls()).toHaveLength(2);
  expect(navigationUrl()).toBe(context("Python", true, 10, identity));
});

it("restores selection by the full identity after reorder, never by position or external ID alone", async () => {
  const other = {
    ...vacancy,
    source_scope: "another-company",
    title: "Other company same ID",
  };
  resetNavigation(context("Python", false, 0, identity));
  fetcher.mockImplementation((url: string) =>
    Promise.resolve(
      url === "/api/profile"
        ? Response.json({})
        : searchResponse(url, [other, vacancy]),
    ),
  );
  const first = render(<Discover />);
  expect(await screen.findByLabelText("Описание вакансии")).toHaveTextContent(
    "Python developer",
  );
  first.unmount();
  fetcher.mockImplementation((url: string) =>
    Promise.resolve(
      url === "/api/profile" ? Response.json({}) : searchResponse(url, [other]),
    ),
  );
  render(<Discover />);
  await screen.findByRole("button", { name: /Other company same ID/ });
  expect(screen.queryByLabelText("Описание вакансии")).not.toBeInTheDocument();
});

it("marks old A as a disabled placeholder on A → B → Back until fresh A, and rejects late B", async () => {
  resetNavigation(context("A", false, 0, identity));
  render(<Discover />);
  await resultCard();
  let finishB!: (value: Response) => void;
  let finishA!: (value: Response) => void;
  fetcher.mockImplementation(
    (url: string) =>
      new Promise<Response>((resolve) => {
        if (url.includes("query=B")) finishB = resolve;
        else finishA = resolve;
      }),
  );
  submit("B");
  act(back);
  expect(screen.queryByLabelText("Описание вакансии")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /Python developer.*Test company/ }),
  ).toBeDisabled();
  expect(
    screen.getByRole("region", { name: "Результаты поиска" }),
  ).toHaveAttribute("aria-busy", "true");
  await act(async () =>
    finishA(
      searchResponse("/api/discover?offset=0", [
        { ...vacancy, title: "Fresh A" },
      ]),
    ),
  );
  await act(async () =>
    finishB(
      searchResponse("/api/discover?offset=0", [
        { ...vacancy, title: "Late B" },
      ]),
    ),
  );
  expect(screen.getByLabelText("Описание вакансии")).toHaveTextContent(
    "Fresh A",
  );
  expect(screen.queryByText("Late B")).not.toBeInTheDocument();
  expect(searchCalls()).toHaveLength(3);
});

it.each([
  "/discover?offset=-1&remote_only=true&extra=1",
  "/discover?query=&offset=10",
])(
  "canonicalizes invalid empty search once without API calls: %s",
  async (url) => {
    resetNavigation(url);
    render(<Discover />);
    await screen.findByRole("button", { name: "QA" });
    expect(navigationUrl()).toBe("/discover");
    expect(testRouter.replace).toHaveBeenCalledTimes(1);
    expect(searchCalls()).toHaveLength(0);
  },
);

it("normalizes invalid filters/unknown params without duplicate requests or replace loops", async () => {
  resetNavigation(
    "/discover?query=Python&remote_only=oops&offset=-1&unknown=x&selected=0",
  );
  render(<Discover />);
  await resultCard();
  expect(navigationUrl()).toBe(context());
  expect(searchCalls()).toHaveLength(1);
  expect(testRouter.replace).toHaveBeenCalledTimes(1);
});

it.each([null, [], "malformed", ["", 3]])(
  "missing/malformed roles do not block ordinary search: %s",
  async (roles) => {
    fetcher.mockImplementation((url: string) =>
      Promise.resolve(
        url === "/api/profile"
          ? Response.json({ target_roles: roles })
          : searchResponse(url),
      ),
    );
    render(<Discover />);
    submit("Python");
    await resultCard();
    expect(screen.queryByLabelText("Роли из профиля")).not.toBeInTheDocument();
  },
);

it("profile failure is optional and a BFCache restore refreshes current search", async () => {
  resetNavigation(context("Python", false, 0, identity));
  fetcher.mockImplementation((url: string) =>
    Promise.resolve(
      url === "/api/profile"
        ? Response.json({ code: "profile_missing" }, { status: 404 })
        : searchResponse(url),
    ),
  );
  render(<Discover />);
  await screen.findByLabelText("Описание вакансии");
  act(() =>
    window.dispatchEvent(
      new PageTransitionEvent("pageshow", { persisted: true }),
    ),
  );
  await waitFor(() => expect(searchCalls()).toHaveLength(2));
  expect(await screen.findByLabelText("Описание вакансии")).toBeInTheDocument();
  expect(
    fetcher.mock.calls.filter(([url]) => url === "/api/profile"),
  ).toHaveLength(1);
});
