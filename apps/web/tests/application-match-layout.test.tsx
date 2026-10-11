import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApplicationStatusDetail } from "../components/application-status-detail";
import { ApplicationView } from "../components/application-detail";
import { CompactMatchSummary } from "../components/match-summary";
import { matchFixture } from "./match-fixtures";
import { saved } from "./fixtures";

const originalUrl = window.location.href;
afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", originalUrl);
});
const lowMatch = () => ({
  ...matchFixture(),
  score: 35,
  verdict: "low" as const,
  coverage: 55,
  confidence: "low" as const,
});
const precedes = (first: Element, second: Element) =>
  expect(
    first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
const renderDetail = () =>
  render(<ApplicationStatusDetail detail={saved} matchApplicationId={42} />);

it("keeps vacancy readable while loading, then composes summary, vacancy, details, CRM and history", async () => {
  let finish!: (value: Response) => void;
  const fetcher = vi.fn(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  const { container } = renderDetail();
  const summary = screen.getByRole("region", { name: "Соответствие профилю" });
  const vacancy = screen.getByRole("region", { name: "О вакансии" });
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
    saved.job.title!,
  );
  expect(within(vacancy).getByText(saved.job.description!)).toBeVisible();
  expect(summary).toHaveAttribute("aria-busy", "true");
  expect(within(summary).getByRole("status")).toHaveTextContent(
    "Сравниваем с профилем…",
  );
  expect(
    within(summary).queryByRole("button", { name: "Подробнее о соответствии" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Подробное соответствие профилю" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Сохранить статус" }),
  ).toBeVisible();
  await act(async () => finish(Response.json(lowMatch())));
  const details = screen.getByRole("region", {
    name: "Подробное соответствие профилю",
  });
  expect(summary).toHaveAttribute("aria-busy", "false");
  expect(within(summary).getByLabelText("Оценка 35 из 100")).toBeVisible();
  expect(
    within(summary).getByText("Сравнимый вес критериев: 55%"),
  ).toBeVisible();
  expect(
    within(summary).getByText("Данных для общей оценки мало."),
  ).toBeVisible();
  expect(
    within(summary).queryByText("Обязательные навыки"),
  ).not.toBeInTheDocument();
  expect(
    within(summary).queryByText("Другие критерии"),
  ).not.toBeInTheDocument();
  expect(within(summary).queryByText("Что проверить")).not.toBeInTheDocument();
  expect(within(details).getByText("Kubernetes")).toBeVisible();
  expect(
    within(details).queryByLabelText(/Оценка \d+ из 100/),
  ).not.toBeInTheDocument();
  expect(screen.getAllByLabelText("Оценка 35 из 100")).toHaveLength(1);
  expect(container.querySelectorAll(".match-summary")).toHaveLength(0);
  precedes(screen.getByRole("link", { name: /Открыть у источника/ }), summary);
  precedes(summary, vacancy);
  precedes(vacancy, details);
  precedes(details, screen.getByRole("heading", { name: "Заметки" }));
  precedes(
    screen.getByRole("heading", { name: "Следующее действие" }),
    screen.getByRole("heading", { name: "История статусов" }),
  );
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("uses a native fragment link, preserves the return query and focuses the details heading", async () => {
  window.history.replaceState(
    null,
    "",
    "/applications/42?from=%2Fapplications",
  );
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(lowMatch())));
  renderDetail();
  const disclosure = await screen.findByRole("button", {
    name: "Подробнее о соответствии",
  });
  fireEvent.click(disclosure);
  const link = screen.getByRole("link", { name: "Перейти к полному разбору" });
  expect(link).toHaveAttribute("href", "#match-details");
  fireEvent.click(link);
  const heading = screen.getByRole("heading", {
    level: 2,
    name: "Подробное соответствие профилю",
  });
  expect(heading).toHaveAttribute("id", "match-details");
  expect(heading).toHaveAttribute("tabindex", "-1");
  expect(heading).toHaveFocus();
  await waitFor(() => expect(window.location.hash).toBe("#match-details"));
  expect(window.location.pathname).toBe("/applications/42");
  expect(window.location.search).toBe("?from=%2Fapplications");
});

it("keeps a single retry in the compact error state without displacing vacancy content", async () => {
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("offline"))
    .mockResolvedValueOnce(Response.json(lowMatch()));
  vi.stubGlobal("fetch", fetcher);
  renderDetail();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Не удалось загрузить оценку.",
  );
  expect(
    screen.queryByRole("button", { name: "Подробнее о соответствии" }),
  ).not.toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Повторить" })).toHaveLength(1);
  const vacancy = screen.getByRole("region", { name: "О вакансии" });
  expect(within(vacancy).getByText(saved.job.description!)).toBeVisible();
  expect(
    screen.queryByRole("region", { name: "Подробное соответствие профилю" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
  expect(await screen.findByLabelText("Оценка 35 из 100")).toBeVisible();
  precedes(
    vacancy,
    screen.getByRole("region", { name: "Подробное соответствие профилю" }),
  );
  expect(
    screen.queryByRole("button", { name: "Повторить" }),
  ).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("keeps missing-profile actions above vacancy and omits unavailable full details", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ code: "PROFILE_REQUIRED" }, { status: 409 }),
      ),
  );
  renderDetail();
  expect(await screen.findByText("Для сравнения нужен профиль.")).toBeVisible();
  const summary = screen.getByRole("region", { name: "Соответствие профилю" });
  const vacancy = screen.getByRole("region", { name: "О вакансии" });
  expect(
    within(summary).getByRole("link", { name: "Редактировать профиль" }),
  ).toHaveAttribute("href", "/profile");
  expect(
    within(summary).getByRole("link", { name: "Импортировать CV" }),
  ).toHaveAttribute("href", "/profile/import");
  expect(within(vacancy).getByText(saved.job.description!)).toBeVisible();
  precedes(summary, vacancy);
  expect(
    screen.queryByRole("region", { name: "Подробное соответствие профилю" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Подробнее о соответствии" }),
  ).not.toBeInTheDocument();
});

it("retains insufficient-data coverage without fabricating a zero in the compact summary", () => {
  render(
    <CompactMatchSummary
      result={{
        ...matchFixture(),
        score: null,
        verdict: "insufficient_data",
        coverage: 30,
        confidence: null,
      }}
    />,
  );
  expect(screen.getByText("Недостаточно данных")).toBeVisible();
  expect(screen.getByText("Сравнимый вес критериев: 30%")).toBeVisible();
  expect(screen.queryByLabelText(/Оценка \d+ из 100/)).not.toBeInTheDocument();
});

it("keeps existing requirement content and heading hierarchy when the description is absent", () => {
  render(
    <ApplicationView
      detail={{ ...saved, job: { ...saved.job, description: null } }}
    />,
  );
  const vacancy = screen.getByRole("region", { name: "О вакансии" });
  expect(
    within(vacancy).getByRole("heading", { level: 2, name: "О вакансии" }),
  ).toBeVisible();
  expect(
    within(vacancy).getByRole("heading", { level: 3, name: "Требования" }),
  ).toBeVisible();
  expect(within(vacancy).getByText(saved.job.requirements_text!)).toBeVisible();
});
