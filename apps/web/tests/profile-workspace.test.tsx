import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { ProfileWorkspace } from "../components/profile-workspace";
import { ProfileImportSuccess } from "../components/profile-import-success";
import type { Profile } from "../lib/profile";

const profile: Profile = {
  target_roles: ["Python Engineer"],
  skills: ["Python", "PostgreSQL"],
  experience: "middle",
  location: ["Yerevan"],
  workplace_preference: "remote",
  salary_min: "2500.25",
  salary_currency: "USD",
  salary_period: "month",
  languages: [{ language: "English", level: "B2" }],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};
const work = {
  items: [
    {
      company: "Acme",
      position: "Engineer",
      engagement_kind: "employment",
      start_year: 2020,
      start_month: 2,
      end_year: null,
      end_month: null,
      is_current: true,
      duration_months: 80,
    },
  ],
};
const facts = { items: [{ text: "Built an API" }] };

function stubProfile(initial: Profile | null = profile) {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/profile" && init?.method === "PUT")
      return Response.json({ ok: true });
    if (url === "/api/profile")
      return initial
        ? Response.json(initial)
        : Response.json({ code: "profile_missing" }, { status: 404 });
    if (url === "/api/profile/work-experiences") return Response.json(work);
    if (url === "/api/profile/experience-facts") return Response.json(facts);
    throw new Error(`Unexpected ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("dismisses the import toast after 4.5 seconds under StrictMode without moving focus", () => {
  vi.useFakeTimers();
  window.history.replaceState(null, "", "/profile?imported=1");
  render(
    <StrictMode>
      <button>Existing control</button>
      <ProfileImportSuccess />
    </StrictMode>,
  );
  const control = screen.getByRole("button", { name: "Existing control" });
  control.focus();
  expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
  expect(window.location.search).toBe("");
  act(() => vi.advanceTimersByTime(4499));
  expect(screen.getByRole("status")).toHaveTextContent(
    "Данные резюме применены к профилю.",
  );
  expect(control).toHaveFocus();
  act(() => vi.advanceTimersByTime(1));
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(control).toHaveFocus();
  window.history.replaceState(null, "", "/");
});

it("renders a lightweight read page with nullable states and read-only experience", async () => {
  const fetcher = stubProfile();
  render(<ProfileWorkspace />);
  expect(
    await screen.findByRole("button", { name: "Редактировать" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Python Engineer")).toBeInTheDocument();
  expect(screen.getByText("PostgreSQL")).toBeInTheDocument();
  expect(screen.getByText("Middle")).toBeInTheDocument();
  expect(screen.getByText("English")).toBeInTheDocument();
  expect(screen.getByText(/2500.25 USD в месяц/)).toBeInTheDocument();
  expect(await screen.findByText("Built an API")).toBeInTheDocument();
  expect(screen.getByText("Engineer")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /Удалить/ }),
  ).not.toBeInTheDocument();
  expect(fetcher.mock.calls[0][0]).toBe("/api/profile");
  expect(fetcher).toHaveBeenCalledTimes(3);
  expect(
    screen.queryByText("Показаны актуальные данные профиля."),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Заполнить профиль из резюме" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Импортировать резюме" }),
  ).toHaveAttribute("href", "/profile/import");
  expect(
    screen.queryByText("Данные резюме применены к профилю."),
  ).not.toBeInTheDocument();
});

it("shows one import success and consumes only its query flag; refresh has no success banner", async () => {
  stubProfile();
  window.history.replaceState(
    { retained: true },
    "",
    "/profile?imported=1&source=cv#skills",
  );
  const view = render(
    <StrictMode>
      <ProfileImportSuccess />
      <ProfileWorkspace />
    </StrictMode>,
  );
  await screen.findByText("Built an API");
  expect(
    screen.getAllByText("Данные резюме применены к профилю."),
  ).toHaveLength(1);
  expect(screen.getAllByRole("status")).toHaveLength(1);
  expect(
    window.location.pathname + window.location.search + window.location.hash,
  ).toBe("/profile?source=cv#skills");
  expect(window.history.state).toEqual({ retained: true });
  view.unmount();
  // A fresh server render uses the cleaned URL, so it omits ProfileImportSuccess.
  render(<ProfileWorkspace />);
  await screen.findByText("Built an API");
  expect(
    screen.queryByText("Данные резюме применены к профилю."),
  ).not.toBeInTheDocument();
  window.history.replaceState(null, "", "/");
});

it("loads the profile and read-only lists once under StrictMode effect replay", async () => {
  const fetcher = stubProfile();
  render(
    <StrictMode>
      <ProfileWorkspace />
    </StrictMode>,
  );
  expect(await screen.findByText("Built an API")).toBeInTheDocument();
  expect(screen.getByText("Python Engineer")).toBeInTheDocument();
  expect(
    fetcher.mock.calls.filter((call) => call[0] === "/api/profile"),
  ).toHaveLength(1);
  expect(
    fetcher.mock.calls.filter(
      (call) => call[0] === "/api/profile/work-experiences",
    ),
  ).toHaveLength(1);
  expect(
    fetcher.mock.calls.filter(
      (call) => call[0] === "/api/profile/experience-facts",
    ),
  ).toHaveLength(1);
});

it("starts a new initial GET on a real remount and ignores an unmounted response", async () => {
  let finish!: (response: Response) => void;
  const fetcher = vi.fn((url: string) => {
    if (url === "/api/profile") {
      if (fetcher.mock.calls.length === 1)
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      return Promise.resolve(Response.json(profile));
    }
    return Promise.resolve(Response.json({ items: [] }));
  });
  vi.stubGlobal("fetch", fetcher);
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  try {
    const view = render(<ProfileWorkspace />);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    view.unmount();
    await act(async () => finish(Response.json(profile)));
    expect(consoleError).not.toHaveBeenCalled();
    render(<ProfileWorkspace />);
    expect(
      await screen.findByRole("button", { name: "Редактировать" }),
    ).toBeInTheDocument();
    expect(
      fetcher.mock.calls.filter((call) => call[0] === "/api/profile"),
    ).toHaveLength(2);
  } finally {
    consoleError.mockRestore();
  }
});

it("manual refresh makes a new GET after the initial request fails", async () => {
  const fetcher = vi.fn(async (url: string) => {
    if (url === "/api/profile")
      return fetcher.mock.calls.length === 1
        ? Response.json({ code: "api_unavailable" }, { status: 503 })
        : Response.json(profile);
    return Response.json({ items: [] });
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <StrictMode>
      <ProfileWorkspace />
    </StrictMode>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Обновить данные" }),
  );
  expect(await screen.findByText("Python Engineer")).toBeInTheDocument();
  expect(
    fetcher.mock.calls.filter((call) => call[0] === "/api/profile"),
  ).toHaveLength(2);
});

it("shows a legacy language and requires an explicit supported level before PUT", async () => {
  const legacy: Profile = {
    ...profile,
    languages: [{ language: "English English", level: "English" }],
  };
  const fetcher = stubProfile(legacy);
  render(<ProfileWorkspace />);
  expect(await screen.findByText("English English")).toBeInTheDocument();
  expect(screen.getByText("English English").closest("li")).toHaveTextContent(
    "English English · English",
  );
  fireEvent.click(screen.getByRole("button", { name: "Редактировать" }));
  const level = screen.getByRole("combobox", { name: /^Уровень 1 / });
  expect(level).toHaveTextContent("Прежний уровень: English");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(
    screen.getByText(/Укажите язык и допустимый уровень/),
  ).toBeInTheDocument();
  expect(fetcher.mock.calls.some((call) => call[1]?.method === "PUT")).toBe(
    false,
  );
  fireEvent.click(level);
  fireEvent.click(screen.getByRole("option", { name: "B2" }));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some((call) => call[1]?.method === "PUT")).toBe(
      true,
    ),
  );
  const sent = fetcher.mock.calls.find((call) => call[1]?.method === "PUT");
  expect(JSON.parse(sent![1]!.body as string).languages).toEqual([
    { language: "English English", level: "B2" },
  ]);
});

it("opens a single edit form and Cancel discards it without PUT", async () => {
  const fetcher = stubProfile();
  render(<ProfileWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: "Редактировать" }));
  const role = screen.getByRole("textbox", { name: "Роль" });
  fireEvent.change(role, { target: { value: "Changed role" } });
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(screen.getByText("Python Engineer")).toBeInTheDocument();
  expect(screen.queryByText("Changed role")).not.toBeInTheDocument();
  expect(fetcher.mock.calls.every((call) => call[1]?.method !== "PUT")).toBe(
    true,
  );
});

it("uses accessible listboxes for every Profile enum and keeps selections in the draft", async () => {
  const fetcher = stubProfile();
  render(<ProfileWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: "Редактировать" }));
  const experience = screen.getByRole("combobox", { name: /^Уровень опыта / });
  const workplace = screen.getByRole("combobox", { name: /^Формат работы / });
  const salaryPeriod = screen.getByRole("combobox", { name: /^Период / });
  const language = screen.getByRole("combobox", { name: /^Уровень 1 / });
  for (const control of [experience, workplace, salaryPeriod, language]) {
    expect(control).toHaveAttribute("aria-haspopup", "listbox");
    expect(control).toHaveAttribute("aria-expanded", "false");
    expect(control).toHaveAttribute("aria-controls");
  }
  expect(
    new Set(
      [experience, workplace, salaryPeriod, language].map((control) =>
        control.getAttribute("aria-controls"),
      ),
    ).size,
  ).toBe(4);

  fireEvent.keyDown(experience, { key: "End" });
  expect(experience).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("option", { name: "Не указан" })).toHaveAttribute(
    "aria-selected",
    "false",
  );
  fireEvent.keyDown(experience, { key: "Enter" });
  expect(experience).toHaveTextContent("Не указан");
  expect(experience).toHaveFocus();

  fireEvent.click(workplace);
  fireEvent.click(screen.getByRole("option", { name: "Гибрид" }));
  fireEvent.click(salaryPeriod);
  fireEvent.click(screen.getByRole("option", { name: "в год" }));
  expect(workplace).toHaveTextContent("Гибрид");
  expect(salaryPeriod).toHaveTextContent("в год");
  expect(fetcher.mock.calls.every((call) => call[1]?.method !== "PUT")).toBe(
    true,
  );
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(screen.getByText("Middle")).toBeInTheDocument();
  expect(screen.getByText("Удалённо")).toBeInTheDocument();
});

it("keeps repeated language dropdowns independent and exposes field errors", async () => {
  stubProfile();
  render(<ProfileWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: "Редактировать" }));
  fireEvent.click(screen.getByRole("button", { name: "+ Добавить язык" }));
  const first = screen.getByRole("combobox", { name: /^Уровень 1 / });
  const second = screen.getByRole("combobox", { name: /^Уровень 2 / });
  expect(first.id).not.toBe(second.id);
  expect(first.getAttribute("aria-controls")).not.toBe(
    second.getAttribute("aria-controls"),
  );
  fireEvent.click(second);
  fireEvent.click(screen.getByRole("option", { name: "native" }));
  expect(second).toHaveTextContent("native");
  expect(first).toHaveTextContent("B2");
  fireEvent.click(first);
  fireEvent.click(screen.getByRole("option", { name: "C1" }));
  expect(first).toHaveTextContent("C1");
  expect(second).toHaveTextContent("native");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(first).toHaveAttribute("aria-invalid", "true");
  expect(second).toHaveAttribute("aria-invalid", "true");
  expect(first).toHaveAttribute("aria-describedby", "profile-languages-error");
  expect(
    screen.getByRole("button", { name: "Удалить язык, запись 2" }),
  ).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "Удалить язык, запись 2" }),
  );
  expect(
    screen.queryByRole("combobox", { name: /^Уровень 2 / }),
  ).not.toBeInTheDocument();
});

it("navigates a Profile dropdown by keyboard and closes without selecting", async () => {
  const fetcher = stubProfile();
  render(<ProfileWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: "Редактировать" }));
  const trigger = screen.getByRole("combobox", { name: /^Уровень опыта / });
  trigger.focus();
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  const active = () =>
    document.getElementById(trigger.getAttribute("aria-activedescendant")!);
  expect(active()).toHaveTextContent("Senior");
  fireEvent.keyDown(trigger, { key: "ArrowUp" });
  expect(active()).toHaveTextContent("Middle");
  fireEvent.keyDown(trigger, { key: "Home" });
  expect(active()).toHaveTextContent("Стажёр");
  fireEvent.keyDown(trigger, { key: "Escape" });
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(trigger).toHaveTextContent("Middle");
  expect(trigger).toHaveFocus();
  fireEvent.keyDown(trigger, { key: " " });
  fireEvent.keyDown(trigger, { key: "End" });
  expect(active()).toHaveTextContent("Не указан");
  fireEvent.keyDown(trigger, { key: "Tab" });
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(trigger);
  fireEvent.pointerDown(screen.getByRole("button", { name: "Отмена" }));
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(fetcher.mock.calls.every((call) => call[1]?.method !== "PUT")).toBe(
    true,
  );
});

it("creates a missing profile only after a role is entered", async () => {
  const fetcher = stubProfile(null);
  render(<ProfileWorkspace />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Создать профиль" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Проверьте отмеченные поля",
  );
  expect(screen.getByRole("textbox", { name: "Роль" })).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  expect(fetcher.mock.calls.every((call) => call[1]?.method !== "PUT")).toBe(
    true,
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Роль" }), {
    target: { value: "Designer" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some((call) => call[1]?.method === "PUT")).toBe(
      true,
    ),
  );
  const sent = JSON.parse(
    fetcher.mock.calls.find((call) => call[1]?.method === "PUT")?.[1]
      ?.body as string,
  );
  expect(sent).toEqual({
    target_roles: ["Designer"],
    skills: [],
    experience: "unknown",
    location: [],
    workplace_preference: "any",
    salary_min: null,
    salary_currency: null,
    salary_period: "unknown",
    languages: [],
  });
});

it("requires a remaining role and commits pending role and skill text on Save", async () => {
  const fetcher = stubProfile();
  render(<ProfileWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: "Редактировать" }));
  expect(
    screen.getByRole("button", { name: "Удалить роль Python Engineer" }),
  ).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "Удалить роль Python Engineer" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  const role = screen.getByRole("textbox", { name: "Роль" });
  expect(role).toHaveAttribute("aria-invalid", "true");
  expect(role).toHaveAttribute(
    "aria-describedby",
    "profile-target_roles-input-error",
  );
  expect(fetcher.mock.calls.every((call) => call[1]?.method !== "PUT")).toBe(
    true,
  );

  fireEvent.change(role, { target: { value: "  Analyst  " } });
  fireEvent.change(screen.getByRole("textbox", { name: "Навык" }), {
    target: { value: "  Redis  " },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some((call) => call[1]?.method === "PUT")).toBe(
      true,
    ),
  );
  const sent = JSON.parse(
    fetcher.mock.calls.find((call) => call[1]?.method === "PUT")?.[1]
      ?.body as string,
  );
  expect(sent.target_roles).toEqual(["Analyst"]);
  expect(sent.skills).toEqual(["Python", "PostgreSQL", "Redis"]);
  expect(sent.experience).toBe("middle");
  expect(sent.salary_min).toBe("2500.25");
});

it("allows an empty skills array after removing all skill chips", async () => {
  const fetcher = stubProfile();
  render(<ProfileWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: "Редактировать" }));
  fireEvent.click(screen.getByRole("button", { name: "Удалить навык Python" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Удалить навык PostgreSQL" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some((call) => call[1]?.method === "PUT")).toBe(
      true,
    ),
  );
  const sent = JSON.parse(
    fetcher.mock.calls.find((call) => call[1]?.method === "PUT")?.[1]
      ?.body as string,
  );
  expect(sent.skills).toEqual([]);
  expect(sent.target_roles).toEqual(["Python Engineer"]);
});

it("edits repeatable values and sends a complete profile after salary validation", async () => {
  let current = profile;
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/profile" && init?.method === "PUT") {
      current = { ...current, ...JSON.parse(init.body as string) };
      return Response.json({ ok: true });
    }
    if (url === "/api/profile") return Response.json(current);
    if (url.includes("work-experiences")) return Response.json({ items: [] });
    if (url.includes("experience-facts")) return Response.json({ items: [] });
    throw new Error(url);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<ProfileWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: "Редактировать" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Роль" }), {
    target: { value: "Analyst" },
  });
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Роль" }), {
    key: "Enter",
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Удалить навык PostgreSQL" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "+ Добавить язык" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Язык 2" }), {
    target: { value: "Russian" },
  });
  fireEvent.click(screen.getByRole("combobox", { name: /^Уровень 2 / }));
  fireEvent.click(screen.getByRole("option", { name: "native" }));
  fireEvent.click(screen.getByRole("combobox", { name: /^Формат работы / }));
  fireEvent.click(screen.getByRole("option", { name: "Гибрид" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Валюта" }), {
    target: { value: "" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(screen.getByRole("textbox", { name: "Валюта" })).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  expect(fetcher.mock.calls.every((call) => call[1]?.method !== "PUT")).toBe(
    true,
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Валюта" }), {
    target: { value: "usd" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent("подтверждён"),
  );
  const sent = JSON.parse(
    fetcher.mock.calls.find((call) => call[1]?.method === "PUT")?.[1]
      ?.body as string,
  );
  expect(sent).toEqual({
    ...profile,
    target_roles: ["Python Engineer", "Analyst"],
    skills: ["Python"],
    languages: [
      { language: "English", level: "B2" },
      { language: "Russian", level: "native" },
    ],
    workplace_preference: "hybrid",
    salary_currency: "USD",
    created_at: undefined,
    updated_at: undefined,
  });
  expect(
    fetcher.mock.calls
      .filter((call) => call[0] === "/api/profile")
      .map((call) => call[1]?.method ?? "GET"),
  ).toEqual(["GET", "PUT", "GET"]);
});

it("locks double submit and only confirms the value from fresh GET", async () => {
  let finish!: (value: Response) => void;
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(profile))
    .mockResolvedValueOnce(Response.json({ items: [] }))
    .mockResolvedValueOnce(Response.json({ items: [] }))
    .mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValueOnce(
      Response.json({ ...profile, target_roles: ["Backend Engineer"] }),
    );
  vi.stubGlobal("fetch", fetcher);
  render(<ProfileWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: "Редактировать" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Роль" }), {
    target: { value: "Frontend Engineer" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  fireEvent.submit(
    screen.getByRole("button", { name: "Сохранить" }).closest("form")!,
  );
  expect(
    fetcher.mock.calls.filter((call) => call[1]?.method === "PUT"),
  ).toHaveLength(1);
  finish(Response.json({ ok: true }));
  expect(await screen.findByText("Backend Engineer")).toBeInTheDocument();
  expect(screen.queryByText("Frontend Engineer")).not.toBeInTheDocument();
  expect(
    fetcher.mock.calls
      .filter((call) => call[0] === "/api/profile")
      .map((call) => call[1]?.method ?? "GET"),
  ).toEqual(["GET", "PUT", "GET"]);
});

it("keeps confirmed data uncertain after a failed fresh GET; Refresh only reads", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(profile))
    .mockResolvedValueOnce(Response.json({ items: [] }))
    .mockResolvedValueOnce(Response.json({ items: [] }))
    .mockResolvedValueOnce(Response.json({ ok: true }))
    .mockResolvedValueOnce(
      Response.json({ code: "api_unavailable" }, { status: 503 }),
    )
    .mockResolvedValueOnce(
      Response.json({ ...profile, target_roles: ["Updated"] }),
    );
  vi.stubGlobal("fetch", fetcher);
  render(<ProfileWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: "Редактировать" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Роль" }), {
    target: { value: "Updated" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "не удалось загрузить",
  );
  expect(screen.getByText("Python Engineer")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Обновить данные" }));
  expect(await screen.findByText("Updated")).toBeInTheDocument();
  expect(
    fetcher.mock.calls
      .map((call) => call[1]?.method ?? "GET")
      .filter((method) => method === "PUT"),
  ).toHaveLength(1);
});

it("treats a failed PUT as ambiguous and never repeats it automatically", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(profile))
    .mockResolvedValueOnce(Response.json({ items: [] }))
    .mockResolvedValueOnce(Response.json({ items: [] }))
    .mockRejectedValueOnce(new Error("timeout"))
    .mockResolvedValueOnce(Response.json(profile));
  vi.stubGlobal("fetch", fetcher);
  render(<ProfileWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: "Редактировать" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Роль" }), {
    target: { value: "Updated" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "могли сохраниться",
  );
  fireEvent.click(screen.getByRole("button", { name: "Обновить данные" }));
  await waitFor(() =>
    expect(screen.getByText("Python Engineer")).toBeInTheDocument(),
  );
  expect(
    fetcher.mock.calls.filter((call) => call[1]?.method === "PUT"),
  ).toHaveLength(1);
  expect(
    fetcher.mock.calls
      .filter((call) => call[0] === "/api/profile")
      .map((call) => call[1]?.method ?? "GET"),
  ).toEqual(["GET", "PUT", "GET"]);
});
