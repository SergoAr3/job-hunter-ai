import { render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { Dashboard } from "../components/dashboard";
import { applicationStatuses } from "../lib/applications";
import { statusLabels } from "../lib/application-presentation";
import type {
  ApplicationListItem,
  ApplicationsSummary,
} from "../lib/contracts";
import type { Profile } from "../lib/profile";
const summary: ApplicationsSummary = {
  total: 36,
  status_counts: Object.fromEntries(
    applicationStatuses.map((s, i) => [s, i + 1]),
  ) as ApplicationsSummary["status_counts"],
};
const recent: ApplicationListItem[] = Array.from({ length: 7 }, (_, i) => ({
  app_id: 10 - i,
  title: `Role ${i}`,
  company: `Company ${i}`,
  location: "Ереван",
  status: "saved",
  created_at: "2026-10-03T10:00:00Z",
  workplace_type: "remote",
  parsing_status: "done",
  ai_enrichment_status: "done",
}));
const profile: Profile = {
  target_roles: ["Engineer"],
  skills: ["Python", "SQL"],
  location: ["Ереван"],
  workplace_preference: "remote",
  experience: "middle",
  salary_min: null,
  salary_currency: null,
  salary_period: "unknown",
  languages: [],
  created_at: "",
  updated_at: "",
};
it("renders full-collection counts, canonical pipeline, recent five links and real profile", () => {
  render(<Dashboard summary={summary} recent={recent} profile={profile} />);
  const cards = screen.getByRole("region", { name: "Сводка вакансий" });
  expect(within(cards).getByText("36")).toBeInTheDocument();
  expect(
    within(cards)
      .getAllByRole("article")
      .map((card) => card.getAttribute("aria-label")),
  ).toEqual([
    "Всего вакансий",
    "Сохранено",
    "Отклик отправлен",
    "Собеседование",
  ]);
  expect(within(cards).queryByText("Оффер")).not.toBeInTheDocument();
  expect(screen.queryByText("ВАШ ПОИСК РАБОТЫ")).not.toBeInTheDocument();

  const pipeline = screen.getByRole("region", { name: "Воронка поиска" });
  applicationStatuses.forEach((s, i) => {
    expect(
      within(pipeline).getByText(statusLabels[s]).parentElement,
    ).toHaveTextContent(String(i + 1));
  });
  const rows = screen.getByRole("region", { name: "Недавние вакансии" });
  expect(within(rows).getAllByRole("link")).toHaveLength(6);
  for (let i = 0; i < 5; i++)
    expect(
      within(rows).getByRole("link", { name: new RegExp(`Role ${i}`) }),
    ).toHaveAttribute("href", `/applications/${10 - i}`);
  expect(screen.queryByText("Role 5")).not.toBeInTheDocument();
  expect(screen.getByText("Engineer")).toBeInTheDocument();
  expect(screen.getByText("Python")).toBeInTheDocument();
  expect(screen.getByText("Формат работы: Удалённо")).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Редактировать профиль →" }),
  ).toHaveAttribute("href", "/profile");
  expect(screen.getByRole("link", { name: "Найти вакансии" })).toHaveAttribute(
    "href",
    "/discover",
  );
  expect(screen.getByRole("link", { name: "Все вакансии →" })).toHaveAttribute(
    "href",
    "/applications",
  );
});
it.each([null, { ...profile, target_roles: [], skills: [] }])(
  "keeps empty dashboard and incomplete profile useful",
  (value) => {
    render(
      <Dashboard
        summary={{
          total: 0,
          status_counts: Object.fromEntries(
            applicationStatuses.map((s) => [s, 0]),
          ) as ApplicationsSummary["status_counts"],
        }}
        recent={[]}
        profile={value}
      />,
    );
    expect(screen.getByText("Начните поиск вакансий")).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Сводка вакансий" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Воронка поиска")).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Заполнить профиль" }),
    ).toHaveAttribute("href", "/profile");
  },
);
it("zero status bars have zero width without invalid values", () => {
  const counts = Object.fromEntries(
    applicationStatuses.map((s) => [s, s === "saved" ? 1 : 0]),
  ) as ApplicationsSummary["status_counts"];
  const { container } = render(
    <Dashboard
      summary={{ total: 1, status_counts: counts }}
      recent={recent.slice(0, 1)}
      profile={profile}
    />,
  );
  const bars = container.querySelectorAll(".dashboard-bar span");
  expect(bars[0]).toHaveStyle({ width: "100%" });
  expect(bars[1]).toHaveStyle({ width: "0%" });
});
