import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApplicationMatch } from "../components/application-match";
import { ApplicationStatusDetail } from "../components/application-status-detail";
import { CompactMatchSummary } from "../components/match-summary";
import { matchFixture } from "./match-fixtures";
import { saved } from "./fixtures";

const originalUrl = window.location.href;
afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", originalUrl);
});
function lowMatch() {
  const result = matchFixture();
  result.score = 35;
  result.verdict = "low";
  result.coverage = 55;
  result.confidence = "low";
  result.components.required_skills.missing = [
    "HTTP",
    "WebSockets",
    "MQ",
    "MSSQL",
    "ElasticSearch",
    "microservices",
  ];
  result.components.role = {
    ...result.components.role,
    score: 0,
    status: "mismatch",
    matched: [],
    missing: ["Backend developer"],
  };
  for (const name of ["seniority", "languages", "workplace", "salary"] as const)
    result.components[name] = {
      ...result.components[name],
      score: null,
      status: "unknown",
      matched: [],
      missing: [],
    };
  return result;
}
const openPreview = () => {
  const button = screen.getByRole("button", {
    name: "Подробнее о соответствии",
  });
  fireEvent.click(button);
  return screen.getByRole("region", { name: "Краткий разбор соответствия" });
};

it("defaults closed and toggles locally without changing URL, history or focus", () => {
  window.history.replaceState(
    null,
    "",
    "/applications/42?from=%2Fapplications#existing",
  );
  const url = window.location.href;
  const historyLength = window.history.length;
  render(<CompactMatchSummary result={lowMatch()} />);
  const button = screen.getByRole("button", {
    name: "Подробнее о соответствии",
  });
  expect(button.tagName).toBe("BUTTON");
  expect(button).toHaveAttribute("type", "button");
  expect(button).toHaveAttribute("aria-expanded", "false");
  const panel = document.getElementById(button.getAttribute("aria-controls")!)!;
  expect(panel).toHaveAttribute("hidden");
  expect(
    screen.queryByRole("region", { name: "Краткий разбор соответствия" }),
  ).not.toBeInTheDocument();
  button.focus();
  fireEvent.click(button);
  expect(button).toHaveAccessibleName("Скрыть подробности");
  expect(button).toHaveAttribute("aria-expanded", "true");
  expect(panel).toBeVisible();
  expect(button).toHaveFocus();
  expect(window.location.href).toBe(url);
  expect(window.history.length).toBe(historyLength);
  fireEvent.click(button);
  expect(button).toHaveAccessibleName("Подробнее о соответствии");
  expect(button).toHaveAttribute("aria-expanded", "false");
  expect(panel).not.toBeVisible();
  expect(button).toHaveFocus();
  expect(window.location.href).toBe(url);
  expect(window.history.length).toBe(historyLength);
});

it("condenses the real low-match shape into three facts without repeating detail labels", () => {
  render(<CompactMatchSummary result={lowMatch()} />);
  const preview = openPreview();
  const rows = within(preview).getAllByRole("listitem");
  expect(rows).toHaveLength(3);
  expect(rows[0]).toHaveTextContent(
    "Не найдено обязательных навыков в профиле: 6",
  );
  expect(rows[1]).toHaveTextContent(
    "Роль расходится с целевыми ролями профиля",
  );
  expect(rows[2]).toHaveTextContent("Недостаточно данных по условиям вакансии");
  for (const label of lowMatch().components.required_skills.missing)
    expect(within(preview).queryByText(label)).not.toBeInTheDocument();
  expect(within(preview).queryByText("Redis")).not.toBeInTheDocument();
  expect(within(preview).queryByRole("definition")).not.toBeInTheDocument();
  expect(
    within(preview).getByRole("link", { name: "Перейти к полному разбору" }),
  ).toHaveAttribute("href", "#match-details");
});

it("reserves room for an aggregated unknown while limiting a crowded preview to four rows", () => {
  const result = lowMatch();
  for (const name of ["seniority", "languages", "workplace"] as const)
    result.components[name] = {
      ...result.components[name],
      score: 0,
      status: "mismatch",
    };
  render(<CompactMatchSummary result={result} />);
  const rows = within(openPreview()).getAllByRole("listitem");
  expect(rows).toHaveLength(4);
  expect(rows[0]).toHaveTextContent(
    "Не найдено обязательных навыков в профиле: 6",
  );
  expect(rows[1]).toHaveTextContent("Роль расходится");
  expect(rows[3]).toHaveTextContent("Недостаточно данных по условиям вакансии");
});

it.each([false, true])(
  "shows factual positives for high match without inventing issues (salary unknown: %s)",
  (salaryUnknown) => {
    const result = matchFixture();
    result.components.required_skills = {
      ...result.components.required_skills,
      score: 100,
      status: "matched",
      missing: [],
    };
    if (salaryUnknown)
      result.components.salary = {
        ...result.components.salary,
        score: null,
        status: "unknown",
      };
    render(<CompactMatchSummary result={result} />);
    const preview = openPreview();
    expect(
      within(preview).getByText("Обязательные навыки совпадают с профилем"),
    ).toBeVisible();
    expect(
      within(preview).getByText("Роль соответствует целевым ролям профиля"),
    ).toBeVisible();
    expect(within(preview).getAllByRole("listitem")).toHaveLength(
      salaryUnknown ? 3 : 2,
    );
    expect(
      within(preview).queryByText(
        /Не найдено|расходится|Рекомендуем откликаться/,
      ),
    ).not.toBeInTheDocument();
    expect(
      within(preview).queryByText(
        "Недостаточно данных по условиям вакансии",
      ) !== null,
    ).toBe(salaryUnknown);
  },
);

it("keeps useful unknown facts available for insufficient data without a diagnostic list or zero score", () => {
  const result = lowMatch();
  result.score = null;
  result.verdict = "insufficient_data";
  result.confidence = null;
  result.components.required_skills = {
    ...result.components.required_skills,
    score: null,
    status: "unknown",
    matched: [],
    missing: [],
  };
  result.components.role = {
    ...result.components.role,
    score: null,
    status: "unknown",
    missing: [],
  };
  render(<CompactMatchSummary result={result} />);
  expect(screen.queryByLabelText(/Оценка \d+ из 100/)).not.toBeInTheDocument();
  const preview = openPreview();
  expect(within(preview).getAllByRole("listitem").length).toBeLessThanOrEqual(
    4,
  );
  expect(
    within(preview).getByText(
      "Требования к обязательным навыкам требуют уточнения",
    ),
  ).toBeVisible();
});

it("includes a real conflict even when its criterion is partial, without claiming positive fit", () => {
  const result = matchFixture();
  result.components.required_skills = {
    ...result.components.required_skills,
    score: 100,
    status: "matched",
    missing: [],
  };
  result.components.workplace.status = "partial";
  result.conflicts = [
    { component: "workplace", code: "workplace_not_preferred", value: null },
  ];
  render(<CompactMatchSummary result={result} />);
  const preview = openPreview();
  expect(
    within(preview).getByText(
      "Формат работы: проверьте расхождение с предпочтениями",
    ),
  ).toBeVisible();
  expect(
    within(preview).queryByText("Обязательные навыки совпадают с профилем"),
  ).not.toBeInTheDocument();
});

it("resets preview to closed when navigating to another application ID", async () => {
  const fetcher = vi.fn((url: string) =>
    Promise.resolve(
      Response.json({
        ...lowMatch(),
        application_id: url.includes("/43/") ? 43 : 42,
      }),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  const { rerender } = render(<ApplicationMatch applicationId={42} />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Подробнее о соответствии" }),
  );
  expect(
    screen.getByRole("button", { name: "Скрыть подробности" }),
  ).toHaveAttribute("aria-expanded", "true");
  rerender(<ApplicationMatch applicationId={43} />);
  expect(
    screen.queryByRole("button", {
      name: /Подробнее о соответствии|Скрыть подробности/,
    }),
  ).not.toBeInTheDocument();
  expect(
    await screen.findByRole("button", { name: "Подробнее о соответствии" }),
  ).toHaveAttribute("aria-expanded", "false");
  expect(
    screen.queryByRole("region", { name: "Краткий разбор соответствия" }),
  ).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("keeps full facts below vacancy after expanding the preview", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(lowMatch())));
  render(<ApplicationStatusDetail detail={saved} matchApplicationId={42} />);
  await screen.findByRole("button", { name: "Подробнее о соответствии" });
  const preview = openPreview();
  const vacancy = screen.getByRole("region", { name: "О вакансии" });
  const details = screen.getByRole("region", {
    name: "Подробное соответствие профилю",
  });
  expect(
    preview.compareDocumentPosition(vacancy) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    vacancy.compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(within(vacancy).getByText(saved.job.description!)).toBeVisible();
  expect(within(details).getByText("HTTP")).toBeVisible();
});
