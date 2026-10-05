import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApplicationsList } from "../components/applications-list";
import { Navigation } from "../components/navigation";

let testUrl = "/applications";
let testPath = "/applications";
const push = vi.fn();
const replace = vi.fn();
const router = { push, replace };
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(testUrl.split("?")[1] ?? ""),
  usePathname: () => testPath,
}));

const row = {
  app_id: 42,
  status: "interview",
  created_at: "2026-09-30T10:00:00Z",
  title: "Python developer",
  company: "Company",
  location: "Москва, Москва",
  workplace_type: "remote",
  parsing_status: "success",
  ai_enrichment_status: "success",
};
const respond = (items: unknown[] = [row], has_next = false) =>
  Response.json({ items, has_next });

beforeEach(() => {
  testUrl = "/applications";
  testPath = "/applications";
  push.mockReset();
  replace.mockReset();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond()));
});
afterEach(() => vi.unstubAllGlobals());

it("marks only the current navigation section, including detail", () => {
  testPath = "/discover";
  const { rerender } = render(<Navigation />);
  expect(screen.getByRole("link", { name: "Найти вакансии" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(
    screen.getByRole("link", { name: "Мои вакансии" }),
  ).not.toHaveAttribute("aria-current");
  testPath = "/applications";
  rerender(<Navigation />);
  expect(screen.getByRole("link", { name: "Мои вакансии" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  testPath = "/applications/42";
  rerender(<Navigation />);
  expect(screen.getByRole("link", { name: "Мои вакансии" })).toHaveAttribute(
    "aria-current",
    "page",
  );
});

it("renders real list fields, nullable fallbacks and unique short links", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      respond([
        row,
        { ...row, app_id: 43 },
        {
          ...row,
          app_id: 44,
          title: null,
          company: null,
          location: null,
          workplace_type: "unknown",
        },
      ]),
    ),
  );
  render(<ApplicationsList />);
  const links = await screen.findAllByRole("link", {
    name: /Python developer/,
  });
  expect(links).toHaveLength(2);
  expect(links[0]).toHaveAccessibleName(
    "Python developer — Company — Москва — запись 42",
  );
  expect(links[1]).toHaveAccessibleName(
    "Python developer — Company — Москва — запись 43",
  );
  expect(links[0]).toHaveAttribute(
    "href",
    "/applications/42?from=%2Fapplications",
  );
  expect(
    screen.getByRole("link", { name: /Вакансия без названия/ }),
  ).toHaveTextContent("Компания не указана");
  expect(
    document.querySelectorAll(".application-row .status-badge"),
  ).toHaveLength(3);
  expect(
    screen.queryByText(/salary|next_action|description/),
  ).not.toBeInTheDocument();
  expect(screen.getByLabelText("Статус")).toBeInTheDocument();
  expect(screen.getByLabelText("Сортировка")).toBeInTheDocument();
});

it("keeps an unusually long source title out of the full accessible name", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        respond([{ ...row, title: "Long title ".repeat(60) }]),
      ),
  );
  render(<ApplicationsList />);
  const link = await screen.findByRole("link", { name: /Long title/ });
  expect(link.getAttribute("aria-label")!.length).toBeLessThan(170);
  expect(link).toHaveAccessibleName(/запись 42$/);
});

it("submits search, clears it, and resets offset when changing filter or sort", async () => {
  testUrl = "/applications?q=Old&status=offer&sort=oldest&offset=10";
  render(<ApplicationsList />);
  await screen.findByRole("link", { name: /Python developer/ });
  expect(
    screen.getByRole("link", { name: /Python developer/ }),
  ).toHaveAttribute(
    "href",
    expect.stringContaining("from=%2Fapplications%3Fq%3DOld"),
  );
  fireEvent.change(screen.getByLabelText("Название или компания"), {
    target: { value: "  Python  " },
  });
  fireEvent.click(screen.getByRole("button", { name: "Найти" }));
  expect(push).toHaveBeenLastCalledWith(
    "/applications?q=Python&status=offer&sort=oldest",
  );
  fireEvent.change(screen.getByLabelText("Название или компания"), {
    target: { value: "" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Найти" }));
  expect(push).toHaveBeenLastCalledWith(
    "/applications?status=offer&sort=oldest",
  );
  fireEvent.change(screen.getByLabelText("Статус"), { target: { value: "" } });
  expect(push).toHaveBeenLastCalledWith("/applications?q=Old&sort=oldest");
  fireEvent.change(screen.getByLabelText("Сортировка"), {
    target: { value: "next_action" },
  });
  expect(push).toHaveBeenLastCalledWith(
    "/applications?q=Old&status=offer&sort=next_action",
  );
});

it("uses has_next and offset for previous/next without a total", async () => {
  testUrl = "/applications?offset=5";
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond([row], true)));
  render(<ApplicationsList />);
  await screen.findByText("Страница 2");
  expect(screen.getByRole("link", { name: "Назад" })).toHaveAttribute(
    "href",
    "/applications",
  );
  expect(screen.getByRole("link", { name: "Вперёд" })).toHaveAttribute(
    "href",
    "/applications?offset=10",
  );
  expect(screen.queryByText(/из \d+ страниц/)).not.toBeInTheDocument();
});

it("retreats from an empty late page and distinguishes empty states", async () => {
  testUrl = "/applications?offset=10";
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() => Promise.resolve(respond([]))),
  );
  const { rerender } = render(<ApplicationsList />);
  await waitFor(() =>
    expect(replace).toHaveBeenCalledWith("/applications?offset=5"),
  );
  testUrl = "/applications?status=offer";
  rerender(<ApplicationsList />);
  expect(
    await screen.findByText("По выбранным условиям ничего не найдено"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Сбросить поиск и фильтр" }),
  ).toHaveAttribute("href", "/applications");
  testUrl = "/applications";
  rerender(<ApplicationsList />);
  expect(
    await screen.findByText("Сохранённых вакансий пока нет"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Перейти к поиску вакансий" }),
  ).toHaveAttribute("href", "/discover");
});

it("returns from an empty second page to the first page", async () => {
  testUrl = "/applications?offset=5";
  const fetcher = vi.fn().mockResolvedValue(respond([]));
  vi.stubGlobal("fetch", fetcher);
  render(<ApplicationsList />);
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/applications"));
  expect(fetcher).toHaveBeenCalledOnce();
});

it("stops after one previous-page attempt when consecutive pages are empty", async () => {
  testUrl = "/applications?offset=15";
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(respond([]))
    .mockResolvedValueOnce(respond([]))
    .mockResolvedValueOnce(respond([row]));
  vi.stubGlobal("fetch", fetcher);
  const { rerender } = render(<ApplicationsList />);
  await waitFor(() =>
    expect(replace).toHaveBeenCalledWith("/applications?offset=10"),
  );
  testUrl = "/applications?offset=10";
  rerender(<ApplicationsList />);
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/applications"));
  testUrl = "/applications";
  rerender(<ApplicationsList />);
  await screen.findByRole("link", { name: /Python developer/ });
  expect(fetcher).toHaveBeenCalledTimes(3);
  expect(replace.mock.calls.map(([url]) => url)).toEqual([
    "/applications?offset=10",
    "/applications",
  ]);
});

it.each(["1", "-1", "NaN", "9007199254740990"])(
  "normalizes invalid offset %s before any GET",
  async (offset) => {
    testUrl = `/applications?offset=${offset}`;
    const fetcher = vi.fn().mockResolvedValue(respond());
    vi.stubGlobal("fetch", fetcher);
    const { rerender } = render(<ApplicationsList />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/applications"));
    expect(fetcher).not.toHaveBeenCalled();
    testUrl = "/applications";
    rerender(<ApplicationsList />);
    await screen.findByRole("link", { name: /Python developer/ });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher.mock.calls[0][0]).toContain("offset=0");
  },
);

it("canonicalizes invalid URL values before making a request", async () => {
  testUrl = "/applications?status=bad&sort=bad&offset=-1";
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  render(<ApplicationsList />);
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/applications"));
  expect(fetcher).not.toHaveBeenCalled();
});

it("shows only compact next action when present and keeps full notes off list rows", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      respond([
        {
          ...row,
          next_action: "Написать рекрутеру",
          note: "Private detail-only note",
        },
      ]),
    ),
  );
  render(<ApplicationsList />);
  expect(await screen.findByText("Следующее: Написать рекрутеру")).toHaveClass(
    "application-row-next-action",
  );
  expect(
    screen.queryByText("Private detail-only note"),
  ).not.toBeInTheDocument();
});
