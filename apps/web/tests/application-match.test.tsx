import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApplicationMatch, MatchFacts } from "../components/application-match";
import { ApplicationStatusDetail } from "../components/application-status-detail";
import { Matching } from "../components/vacancy";
import { MatchSummary } from "../components/match-summary";
import { matchFixture } from "./match-fixtures";
import { saved, vacancy } from "./fixtures";
const response = (data: unknown, status = 200) =>
  Response.json(data, { status });
afterEach(() => vi.unstubAllGlobals());

it("auto-loads locally without blocking vacancy/CRM and renders facts once", async () => {
  let finish!: (value: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    ),
  );
  const { container } = render(
    <ApplicationStatusDetail detail={saved} matchApplicationId={42} />,
  );
  expect(screen.getByRole("status")).toHaveTextContent(
    "Сравниваем с профилем…",
  );
  expect(
    screen.getByRole("region", { name: "Соответствие профилю" }),
  ).toHaveAttribute("aria-busy", "true");
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
    saved.job.title!,
  );
  expect(
    screen.getByRole("button", { name: "Сохранить статус" }),
  ).toBeInTheDocument();
  finish(response(matchFixture()));
  expect(await screen.findByLabelText("Оценка 80 из 100")).toBeInTheDocument();
  expect(
    within(
      container.querySelector(".application-match") as HTMLElement,
    ).getAllByText("Python", { exact: true }),
  ).toHaveLength(1);
  expect(screen.getAllByText("Kubernetes", { exact: true })).toHaveLength(1);
  expect(
    screen.queryByText(/Стоит откликнуться|Можно откликнуться/),
  ).not.toBeInTheDocument();
  const section = container.querySelector(".application-match")!;
  const notes =
    container.querySelector(".application-notes-editor") ??
    screen.getByRole("heading", { name: "Ваши записи" }).parentElement!;
  expect(
    section.compareDocumentPosition(notes) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});
it("keeps required gaps distinct from calm preferred gaps", () => {
  const { container } = render(<MatchFacts result={matchFixture()} />);
  const required = screen.getByRole("region", { name: "Обязательные навыки" });
  const preferred = screen.getByRole("region", { name: "Желательные навыки" });
  expect(within(required).getByText("Kubernetes")).toBeInTheDocument();
  expect(within(preferred).getByText("Redis")).toBeInTheDocument();
  expect(container.querySelector(".match-preferred-gap")).toHaveTextContent(
    "Redis",
  );
  expect(
    within(screen.getByRole("region", { name: "Что проверить" })).queryByText(
      "Kubernetes",
    ),
  ).not.toBeInTheDocument();
});
it("shows profile CTA for 409 without a zero score", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(response({ code: "PROFILE_REQUIRED" }, 409)),
  );
  render(<ApplicationMatch applicationId={42} />);
  expect(
    await screen.findByText("Для сравнения нужен профиль."),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Редактировать профиль" }),
  ).toHaveAttribute("href", "/profile");
  expect(
    screen.getByRole("link", { name: "Импортировать CV" }),
  ).toHaveAttribute("href", "/profile/import");
  expect(screen.queryByLabelText(/Оценка \d+ из 100/)).not.toBeInTheDocument();
});
it("reports errors, retries and never retains a stale confirmed score", async () => {
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("timeout"))
    .mockResolvedValueOnce(response(matchFixture()))
    .mockRejectedValueOnce(new TypeError("offline"));
  vi.stubGlobal("fetch", fetcher);
  render(<ApplicationMatch applicationId={42} />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Не удалось загрузить оценку.",
  );
  fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
  expect(await screen.findByLabelText("Оценка 80 из 100")).toBeInTheDocument();
  const event = new Event("pageshow");
  Object.defineProperty(event, "persisted", { value: true });
  fireEvent(window, event);
  expect(screen.queryByLabelText("Оценка 80 из 100")).not.toBeInTheDocument();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Не удалось загрузить оценку.",
  );
});
it("aborts unmount/application change and ignores a late response", async () => {
  const finishes: ((value: Response) => void)[] = [];
  const signals: AbortSignal[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((resolve) => {
          finishes.push(resolve);
          signals.push(init.signal!);
        }),
    ),
  );
  const { rerender, unmount } = render(<ApplicationMatch applicationId={42} />);
  rerender(<ApplicationMatch applicationId={43} />);
  expect(signals[0].aborted).toBe(true);
  await act(async () => finishes[0](response(matchFixture())));
  expect(screen.queryByLabelText("Оценка 80 из 100")).not.toBeInTheDocument();
  await act(async () =>
    finishes[1](response({ ...matchFixture(), application_id: 43 })),
  );
  expect(screen.getByLabelText("Оценка 80 из 100")).toBeInTheDocument();
  unmount();
  expect(signals[1].aborted).toBe(true);
});
it("shows valid zero but suppresses null/insufficient data, retaining facts and coverage", () => {
  const fixture = matchFixture();
  const { rerender } = render(
    <MatchFacts result={{ ...fixture, score: 0, verdict: "low" }} />,
  );
  expect(screen.getByLabelText("Оценка 0 из 100")).toBeInTheDocument();
  rerender(
    <MatchFacts
      result={{
        ...fixture,
        score: null,
        verdict: "insufficient_data",
        confidence: null,
        coverage: 30,
      }}
    />,
  );
  expect(screen.queryByLabelText(/Оценка \d+ из 100/)).not.toBeInTheDocument();
  expect(screen.getByText(/суммарным весом 30%/)).toBeInTheDocument();
  expect(screen.getByText("Kubernetes")).toBeInTheDocument();
});
it("keeps high score with a low confidence caveat and no apply instruction", () => {
  render(
    <MatchFacts
      result={{
        ...matchFixture(),
        score: 100,
        coverage: 45,
        confidence: "low",
      }}
    />,
  );
  expect(screen.getByLabelText("Оценка 100 из 100")).toBeInTheDocument();
  expect(
    screen.getByText(
      "Данных для общей оценки мало. Балл относится только к сравнимым критериям.",
    ),
  ).toBeInTheDocument();
  expect(
    screen.queryByText(/Стоит откликнуться|Известно 45% данных/),
  ).not.toBeInTheDocument();
});
it("uses unknown lists rather than legacy false language gaps and keeps skill duplicates", () => {
  const fixture = matchFixture();
  fixture.components.required_skills = {
    ...fixture.components.required_skills,
    score: null,
    status: "unknown",
    matched: [],
    missing: [],
  };
  fixture.explainability!.components.required_skills = {
    notices: [
      { code: "profile_skills_missing", source: "profile", value: null },
    ],
    unknown_items: ["Python", "Python"].map((value) => ({
      code: "profile_skills_missing",
      source: "profile",
      value,
    })),
  };
  fixture.explainability!.components.languages.unknown_items = [
    {
      code: "profile_language_level_unknown",
      source: "profile",
      value: "German B1",
    },
    {
      code: "language_requirement_unparseable",
      source: "job",
      value: "French conversational",
    },
  ];
  fixture.gaps.push({
    code: "language_not_listed",
    component: "languages",
    value: "German B1",
  });
  render(<MatchFacts result={fixture} />);
  const required = screen.getByRole("region", { name: "Обязательные навыки" });
  expect(
    within(required).queryByText("Не найдено в профиле"),
  ).not.toBeInTheDocument();
  expect(within(required).getAllByText("Python")).toHaveLength(2);
  const details = screen.getByText("German B1").closest("[hidden]")!;
  expect(details).toHaveAttribute("hidden");
  const control = screen
    .getAllByRole("button", { name: "Подробнее" })
    .find((button) => button.getAttribute("aria-controls") === details.id)!;
  fireEvent.click(control);
  expect(screen.getByText("German B1")).toBeVisible();
  expect(screen.getAllByText("German B1")).toHaveLength(1);
  expect(
    screen.queryByText(/Язык не указан в профиле/),
  ).not.toBeInTheDocument();
});
it("provides safe generic fallback without explainability", () => {
  const fixture = matchFixture();
  fixture.explainability = undefined;
  fixture.components.required_skills = {
    ...fixture.components.required_skills,
    score: null,
    status: "unknown",
    matched: [],
    missing: [],
  };
  render(<MatchFacts result={fixture} />);
  expect(
    screen.getByText(
      "Не удалось определить: недостаточно данных для сравнения навыков.",
    ),
  ).toBeInTheDocument();
});
it("does not describe unavailable job skills as no requirements", () => {
  const fixture = matchFixture();
  fixture.input_state.ai_enrichment_status = "failed";
  fixture.components.required_skills = {
    ...fixture.components.required_skills,
    status: "unknown",
    score: null,
    matched: [],
    missing: [],
  };
  fixture.explainability!.components.required_skills.notices = [
    { code: "job_skills_missing", source: "job", value: null },
  ];
  render(<MatchFacts result={fixture} />);
  expect(
    screen.getByText(
      "В данных вакансии нет списка обязательных навыков для сравнения.",
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      /Данные о навыках и языковых требованиях вакансии недоступны/,
    ),
  ).toBeInTheDocument();
});
it("supports disclosures, long labels and focus links without losing facts", () => {
  const fixture = matchFixture();
  fixture.components.required_skills.matched = Array.from(
    { length: 8 },
    (_, index) => `LongSkill${index}${"VeryLong".repeat(40)}`,
  );
  render(<MatchFacts result={fixture} />);
  const button = screen.getByRole("button", { name: "Показать все (8)" });
  button.focus();
  expect(button).toHaveFocus();
  expect(button).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(button);
  expect(button).toHaveAttribute("aria-expanded", "true");
  expect(
    screen.getByText(fixture.components.required_skills.matched[7]),
  ).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("link", { name: /Не найдено обязательных навыков/ }),
  );
  expect(
    screen.getByRole("heading", { name: "Обязательные навыки" }),
  ).toHaveFocus();
  expect(
    screen.getByText("Локация не сравнивалась для удалённого формата."),
  ).toBeInTheDocument();
});
it("renders malformed success as an error instead of zero", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(response({ ...matchFixture(), coverage: "95" })),
  );
  render(<ApplicationMatch applicationId={42} />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Не удалось загрузить оценку.",
  );
});
it("keeps Discover a preview, with Web profile navigation and weighted coverage", () => {
  const { rerender } = render(<Matching preview={vacancy.preview_match} />);
  expect(
    screen.getByRole("link", { name: "Редактировать профиль →" }),
  ).toHaveAttribute("href", "/profile");
  rerender(
    <Matching
      preview={{
        ...vacancy.preview_match,
        available: true,
        verdict: "insufficient_data",
        coverage: 25,
      }}
    />,
  );
  expect(screen.getByText(/суммарным весом 25%/)).toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Обязательные навыки" }),
  ).not.toBeInTheDocument();
});
it("uses legacy helper reasons only for language gaps actually evaluated by the scorer", () => {
  const fixture = matchFixture();
  fixture.components.languages = {
    ...fixture.components.languages,
    score: 0,
    status: "mismatch",
    matched: [],
    missing: ["English C1", "Spanish B2"],
  };
  fixture.gaps = [
    {
      code: "language_level_below_requirement",
      component: "languages",
      value: "English C1",
    },
    {
      code: "language_not_listed",
      component: "languages",
      value: "Spanish B2",
    },
    { code: "language_not_listed", component: "languages", value: "German B1" },
  ];
  fixture.explainability!.components.languages.unknown_items = [
    {
      code: "profile_language_level_unknown",
      source: "profile",
      value: "German B1",
    },
  ];
  render(<MatchFacts result={fixture} />);
  expect(
    screen.getByText("Уровень в профиле ниже требуемого."),
  ).toBeInTheDocument();
  expect(screen.getAllByText("Язык не указан в профиле.")).toHaveLength(1);
  expect(
    screen.getAllByText(
      "Уровень языка в профиле не удалось сравнить с требованием.",
    ),
  ).toHaveLength(1);
});
it("renders arbitrary skill labels as text without inherited object hints", () => {
  const fixture = matchFixture();
  fixture.components.required_skills.matched = [
    "__proto__",
    "constructor",
    "toString",
    "<img src=x onerror=alert(1)>",
  ];
  const { container } = render(<MatchFacts result={fixture} />);
  for (const value of fixture.components.required_skills.matched)
    expect(screen.getByText(value)).toBeInTheDocument();
  expect(container.querySelector("img")).toBeNull();
});

it.each([
  ["high", "Высокое соответствие"],
  ["medium", "Среднее соответствие"],
  ["low", "Низкое соответствие"],
  ["insufficient_data", "Недостаточно данных"],
] as const)(
  "keeps %s summary compact with a controlled methodology disclosure",
  (verdict, label) => {
    render(
      <MatchSummary
        score={verdict === "insufficient_data" ? null : 35}
        verdict={verdict}
        coverage={55}
        confidence={null}
      />,
    );
    expect(screen.getByText(label, { exact: true })).toBeVisible();
    expect(
      screen.getByText("Оценка основана только на сравнимых критериях."),
    ).toBeVisible();
    const helper = screen.getByText(
      "Критерии имеют разный вес. Неизвестные данные исключаются из оценки.",
    );
    expect(helper).not.toBeVisible();
    const button = screen.getByRole("button", { name: "Как считается оценка" });
    button.focus();
    expect(button).toHaveFocus();
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(
      document.getElementById(button.getAttribute("aria-controls")!),
    ).toContainElement(helper);
    fireEvent.click(button);
    expect(helper).toBeVisible();
    expect(button).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(button);
    expect(helper).not.toBeVisible();
  },
);

it("marks every skill with an individual textual status and keeps preferred gaps neutral", () => {
  render(<MatchFacts result={matchFixture()} />);
  const matched = screen.getByText("Python").closest("li")!;
  const requiredGap = screen.getByText("Kubernetes").closest("li")!;
  const preferredGap = screen.getByText("Redis").closest("li")!;
  expect(matched).toHaveClass("match-skill-matched");
  expect(matched).toHaveTextContent("✓");
  expect(requiredGap).toHaveClass("match-skill-missing");
  expect(requiredGap).not.toHaveClass("match-skill-neutral");
  expect(requiredGap).toHaveTextContent("!");
  expect(preferredGap).toHaveClass("match-skill-neutral");
  expect(preferredGap).toHaveTextContent("—");
  expect(screen.getAllByText(/Не найдено означает/)).toHaveLength(1);
});

it("aggregates salary reasons but preserves all source details behind a disclosure", () => {
  const fixture = matchFixture();
  fixture.components.salary = {
    ...fixture.components.salary,
    score: null,
    status: "unknown",
  };
  fixture.explainability!.components.salary.notices = [
    { code: "profile_salary_missing", source: "profile", value: null },
    { code: "profile_salary_currency_missing", source: "profile", value: null },
    { code: "profile_salary_period_unknown", source: "profile", value: null },
    { code: "job_salary_period_unknown", source: "job", value: null },
  ];
  render(<MatchFacts result={fixture} />);
  expect(screen.getByText("Зарплату пока нельзя сравнить.")).toBeVisible();
  expect(
    screen.getAllByText("В профиле не указаны полные зарплатные ожидания."),
  ).toHaveLength(1);
  const detail = screen.getByText(
    "Валюта зарплатных ожиданий не указана в профиле.",
  );
  expect(detail).not.toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Подробнее" }));
  expect(detail).toBeVisible();
  expect(
    screen.getByText("Период зарплаты вакансии неизвестен."),
  ).toBeVisible();
});

it("limits pointers, prioritizes required gaps and mismatches, and aggregates unknown criteria", () => {
  const fixture = matchFixture();
  fixture.components.role = {
    ...fixture.components.role,
    score: 0,
    status: "mismatch",
    matched: [],
    missing: ["A".repeat(500), "B"],
  };
  fixture.components.workplace = {
    ...fixture.components.workplace,
    score: 0,
    status: "mismatch",
  };
  for (const name of ["seniority", "languages", "salary"] as const)
    fixture.components[name] = {
      ...fixture.components[name],
      score: null,
      status: "unknown",
      matched: [],
      missing: [],
    };
  render(<MatchFacts result={fixture} />);
  const pointers = screen.getByRole("region", { name: "Что проверить" });
  expect(within(pointers).getAllByRole("listitem")).toHaveLength(4);
  expect(within(pointers).getAllByRole("link")[0]).toHaveTextContent(
    "Не найдено обязательных навыков",
  );
  expect(
    within(pointers).getByText("Недостаточно данных по условиям вакансии"),
  ).toBeInTheDocument();
  expect(
    within(pointers).queryByText(/Уровень:|Языки:|Зарплата:/),
  ).not.toBeInTheDocument();
  const roles = screen.getByText("A".repeat(500));
  expect(roles).not.toBeVisible();
  fireEvent.click(
    screen.getByRole("button", { name: "Показать роли профиля" }),
  );
  expect(roles).toBeVisible();
  fireEvent.click(
    within(pointers).getByRole("link", {
      name: "Недостаточно данных по условиям вакансии",
    }),
  );
  expect(
    screen.getByRole("heading", { name: "Другие критерии" }),
  ).toHaveFocus();
});

it("keeps each language requirement linked to its shared cause without deduplicating labels", () => {
  const fixture = matchFixture();
  fixture.explainability!.components.languages.unknown_items = [
    "German B1",
    "German B1",
    "German B2",
  ].map((value) => ({
    code: "profile_language_level_unknown",
    source: "profile",
    value,
  }));
  render(<MatchFacts result={fixture} />);
  expect(
    screen.getByText("Часть языковых требований не удалось сравнить."),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Подробнее" }));
  const rows = within(
    screen.getByRole("list", { name: "Не удалось определить" }),
  ).getAllByRole("listitem");
  expect(
    rows.map((row) => row.querySelector("div > span")?.textContent),
  ).toEqual(["German B1", "German B1", "German B2"]);
  for (const row of rows)
    expect(
      within(row).getByText(
        "Уровень языка в профиле не удалось сравнить с требованием.",
      ),
    ).toBeVisible();
  expect(
    screen.getAllByText(
      "Уровень языка в профиле не удалось сравнить с требованием.",
    ),
  ).toHaveLength(3);
  expect(screen.getAllByText("German B1")).toHaveLength(2);
  expect(screen.getByText("German B2")).toBeVisible();
});

it("links mixed profile and job language reasons to each requirement in order, including duplicates", () => {
  const fixture = matchFixture();
  fixture.explainability!.components.languages.unknown_items = [
    {
      code: "profile_language_level_unknown",
      source: "profile",
      value: "German B1",
    },
    {
      code: "language_requirement_unparseable",
      source: "job",
      value: "French conversational",
    },
    {
      code: "profile_language_level_unknown",
      source: "profile",
      value: "German B1",
    },
  ];
  const profileReason =
    "Уровень языка в профиле не удалось сравнить с требованием.";
  const jobReason = "Требование не удалось распознать для сравнения.";
  render(<MatchFacts result={fixture} />);
  expect(
    screen.getAllByText("Часть языковых требований не удалось сравнить."),
  ).toHaveLength(1);
  expect(screen.getByText("French conversational")).not.toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Подробнее" }));
  const rows = within(
    screen.getByRole("list", { name: "Не удалось определить" }),
  ).getAllByRole("listitem");
  expect(rows).toHaveLength(3);
  const expected = [
    ["German B1", profileReason, jobReason],
    ["French conversational", jobReason, profileReason],
    ["German B1", profileReason, jobReason],
  ];
  rows.forEach((row, index) => {
    const [label, reason, otherReason] = expected[index];
    expect(within(row).getByText(label)).toBeVisible();
    expect(within(row).getByText(reason)).toBeVisible();
    expect(within(row).queryByText(otherReason)).not.toBeInTheDocument();
  });
  expect(screen.getAllByText("German B1")).toHaveLength(2);
  expect(screen.getAllByText(profileReason)).toHaveLength(2);
  expect(screen.getAllByText(jobReason)).toHaveLength(1);
});
