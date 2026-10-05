import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ApplicationNotesEditor } from "../components/application-notes-editor";
import { ApplicationStatusDetail } from "../components/application-status-detail";
import { saved } from "./fixtures";

const title = (field: string) =>
  field === "note" ? "Заметки" : "Следующее действие";
const toggle = (field: string) =>
  screen.getByRole("button", { name: title(field) });
const section = (field: string) =>
  within(screen.getByRole("region", { name: title(field) }));
const input = (field: string) =>
  screen.getByRole("textbox", { name: title(field) });
const save = (field: string) =>
  section(field).getByRole("button", { name: "Сохранить" });
const edit = (field: string, value: string) =>
  fireEvent.change(input(field), { target: { value } });
const fresh = (changes: object) =>
  Response.json({
    ...saved,
    application: { ...saved.application, ...changes },
  });
function setup(application = saved.application) {
  const onSaved = vi.fn();
  const result = render(
    <ApplicationNotesEditor application={application} onSaved={onSaved} />,
  );
  return { ...result, onSaved };
}
beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it.each(["note", "next_action"] as const)(
  "expires %s success after four seconds, resets on Save and cleans up in StrictMode",
  async (field) => {
    vi.useFakeTimers();
    const timeouts = vi.spyOn(globalThis, "setTimeout");
    const clear = vi.spyOn(globalThis, "clearTimeout");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(fresh({ [field]: "First" }))
        .mockResolvedValueOnce(fresh({ [field]: "Second" }))
        .mockResolvedValueOnce(fresh({ [field]: "Third" })),
    );
    const { unmount } = render(
      <StrictMode>
        <ApplicationNotesEditor
          application={saved.application}
          onSaved={vi.fn()}
        />
      </StrictMode>,
    );
    const message =
      field === "note" ? "Заметки сохранены." : "Следующее действие сохранено.";
    async function saveValue(value: string) {
      const before = timeouts.mock.calls.filter(
        (call) => call[1] === 4000,
      ).length;
      fireEvent.click(toggle(field));
      edit(field, value);
      await act(async () => fireEvent.click(save(field)));
      expect(toggle(field)).toHaveAttribute("aria-expanded", "false");
      expect(section(field).getByRole("status")).toHaveTextContent(message);
      expect(
        section(field).getByText(value, {
          selector: "p.application-note-summary",
        }),
      ).toBeInTheDocument();
      expect(
        timeouts.mock.calls.filter((call) => call[1] === 4000),
      ).toHaveLength(before + 1);
    }
    await saveValue("First");
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    await saveValue("Second");
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(section(field).getByRole("status")).toHaveTextContent(message);
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(section(field).queryByRole("status")).toBeNull();
    expect(
      section(field).getByText("Second", {
        selector: "p.application-note-summary",
      }),
    ).toBeInTheDocument();
    await saveValue("Third");
    const lastTimer = timeouts.mock.calls.findLastIndex(
      (call) => call[1] === 4000,
    );
    unmount();
    expect(clear).toHaveBeenCalledWith(timeouts.mock.results[lastTimer].value);
  },
);

it("does not auto-dismiss field errors", async () => {
  vi.useFakeTimers();
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ code: "application_invalid" }, { status: 422 }),
      ),
  );
  setup();
  fireEvent.click(toggle("note"));
  edit("note", "Draft");
  await act(async () => fireEvent.click(save("note")));
  await act(async () => vi.advanceTimersByTimeAsync(5000));
  expect(section("note").getByRole("alert")).toBeInTheDocument();
  expect(input("note")).toHaveValue("Draft");
  expect(toggle("note")).toHaveAttribute("aria-expanded", "true");
});

it("starts both collapsed with persisted summaries, semantic buttons and panels", () => {
  setup();
  expect(
    screen.queryByRole("heading", { name: "Заметки и следующее действие" }),
  ).toBeNull();
  for (const field of ["note", "next_action"]) {
    expect(toggle(field)).toHaveAttribute("aria-expanded", "false");
    expect(
      document.getElementById(toggle(field).getAttribute("aria-controls")!),
    ).toHaveAttribute("hidden");
  }
  expect(screen.queryAllByRole("textbox")).toHaveLength(0);
  expect(
    screen.getByText(saved.application.note!, {
      selector: "p.application-note-summary",
    }),
  ).toHaveClass("application-note-excerpt");
  expect(
    screen.getByText(saved.application.next_action!, {
      selector: "p.application-note-summary",
    }),
  ).toBeInTheDocument();
  expect(screen.getByText(/Существующая дата/)).toHaveTextContent("2026-10-01");
  expect(document.querySelector('input[type="date"]')).toBeNull();
});

it("shows empty summaries and allows both sections open independently", () => {
  setup({
    ...saved.application,
    note: null,
    next_action: null,
    next_action_due_on: null,
  });
  expect(screen.getByText("Нет заметок")).toBeInTheDocument();
  expect(screen.getByText("Не задано")).toBeInTheDocument();
  fireEvent.click(toggle("note"));
  fireEvent.click(toggle("next_action"));
  expect(screen.getAllByRole("textbox")).toHaveLength(2);
  expect(input("note")).toHaveAttribute("rows", "4");
  expect(input("next_action")).toHaveAttribute("rows", "1");
  expect(input("next_action")).toHaveAttribute(
    "placeholder",
    "Написать рекрутеру",
  );
  expect(screen.getByText("До 1000 символов.")).toBeInTheDocument();
  expect(screen.getByText("До 500 символов.")).toBeInTheDocument();
  fireEvent.click(toggle("note"));
  expect(toggle("note")).toHaveAttribute("aria-expanded", "false");
  expect(toggle("next_action")).toHaveAttribute("aria-expanded", "true");
});

it("handles keyboard-origin button activation and keeps focus on the header", () => {
  setup();
  const header = toggle("note");
  header.focus();
  fireEvent.click(header, { detail: 0 });
  expect(header).toHaveAttribute("aria-expanded", "true");
  expect(header).toHaveFocus();
  fireEvent.click(header, { detail: 0 });
  expect(header).toHaveAttribute("aria-expanded", "false");
});

it.each(["note", "next_action"])(
  "saves only %s, blocks repeat submit, collapses and updates summary",
  async (field) => {
    let finish!: (value: Response) => void;
    const fetcher = vi.fn().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetcher);
    const { onSaved } = setup();
    fireEvent.click(toggle(field));
    edit(field, "  Changed  ");
    expect(save(field)).toBeEnabled();
    fireEvent.click(save(field));
    fireEvent.submit(save(field).closest("form")!);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
      [field]: "Changed",
    });
    expect(save(field)).toBeDisabled();
    expect(input(field)).toBeDisabled();
    expect(onSaved).not.toHaveBeenCalled();
    finish(fresh({ [field]: "Changed" }));
    await waitFor(() =>
      expect(toggle(field)).toHaveAttribute("aria-expanded", "false"),
    );
    expect(
      screen.getByText("Changed", { selector: "p.application-note-summary" }),
    ).toBeInTheDocument();
    expect(toggle(field)).toHaveFocus();
    expect(onSaved).toHaveBeenCalledWith(
      expect.objectContaining({ [field]: "Changed" }),
      field,
    );
  },
);

it.each(["note", "next_action"])(
  "Cancel %s restores confirmed text, collapses, focuses header without mutation",
  (field) => {
    setup();
    fireEvent.click(toggle("note"));
    fireEvent.click(toggle("next_action"));
    edit("note", "Note draft");
    edit("next_action", "Action draft");
    fireEvent.click(section(field).getByRole("button", { name: "Отмена" }));
    expect(toggle(field)).toHaveAttribute("aria-expanded", "false");
    expect(toggle(field)).toHaveFocus();
    const other = field === "note" ? "next_action" : "note";
    expect(input(other)).toHaveValue(
      other === "note" ? "Note draft" : "Action draft",
    );
    fireEvent.click(toggle(field));
    expect(input(field)).toHaveValue(
      saved.application[field as "note" | "next_action"],
    );
    expect(save(field)).toBeDisabled();
    expect(fetch).not.toHaveBeenCalled();
  },
);

it.each(["note", "next_action"])(
  "keeps %s open on API error with associated feedback, without changing other section",
  async (field) => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          Response.json({ code: "application_invalid" }, { status: 422 }),
        ),
    );
    const { onSaved } = setup();
    fireEvent.click(toggle(field));
    edit(field, "Draft");
    fireEvent.click(save(field));
    expect(await section(field).findByRole("alert")).toHaveTextContent(
      "до 500",
    );
    expect(toggle(field)).toHaveAttribute("aria-expanded", "true");
    expect(input(field)).toHaveAttribute(
      "aria-describedby",
      expect.stringContaining(`application-${field}-error`),
    );
    expect(toggle(field === "note" ? "next_action" : "note")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(onSaved).not.toHaveBeenCalled();
    expect(save(field)).toBeEnabled();
  },
);

it("preserves another section's unsaved draft when a save completes", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(fresh({ note: "Persisted" })),
  );
  setup();
  fireEvent.click(toggle("note"));
  fireEvent.click(toggle("next_action"));
  edit("note", "Persisted");
  edit("next_action", "Unsaved action");
  fireEvent.click(save("note"));
  await waitFor(() =>
    expect(toggle("note")).toHaveAttribute("aria-expanded", "false"),
  );
  expect(input("next_action")).toHaveValue("Unsaved action");
  expect(save("next_action")).toBeEnabled();
});

it("retains hidden drafts and shows confirmed summary until save succeeds", () => {
  setup();
  fireEvent.click(toggle("note"));
  edit("note", "Draft");
  fireEvent.click(toggle("note"));
  expect(
    section("note").getByText(saved.application.note!, {
      selector: "p.application-note-summary",
    }),
  ).toBeInTheDocument();
  expect(section("note").getByRole("status")).toHaveTextContent(
    "несохранённые",
  );
  fireEvent.click(toggle("note"));
  expect(input("note")).toHaveValue("Draft");
});

it.each(["note", "next_action"])(
  "clears %s with null and reflects empty summary",
  async (field) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        fresh({
          [field]: null,
          ...(field === "next_action" ? { next_action_due_on: null } : {}),
        }),
      ),
    );
    setup();
    fireEvent.click(toggle(field));
    edit(field, " \n ");
    fireEvent.click(save(field));
    await waitFor(() =>
      expect(toggle(field)).toHaveAttribute("aria-expanded", "false"),
    );
    expect(
      screen.getByText(field === "note" ? "Нет заметок" : "Не задано"),
    ).toBeInTheDocument();
    expect(
      JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string),
    ).toEqual({ [field]: null });
    if (field === "next_action")
      expect(screen.queryByText(/Существующая дата/)).toBeNull();
  },
);

it("recovers a lost notes response with GET while retaining the action draft", async () => {
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new Error("lost"))
    .mockResolvedValueOnce(fresh({ note: "Persisted" }));
  vi.stubGlobal("fetch", fetcher);
  setup();
  fireEvent.click(toggle("note"));
  fireEvent.click(toggle("next_action"));
  edit("note", "Persisted");
  edit("next_action", "Other draft");
  fireEvent.click(save("note"));
  await section("note").findByRole("alert");
  expect(save("note")).toBeDisabled();
  fireEvent.click(
    section("note").getByRole("button", { name: "Проверить сохранение" }),
  );
  await waitFor(() =>
    expect(toggle("note")).toHaveAttribute("aria-expanded", "false"),
  );
  expect(input("next_action")).toHaveValue("Other draft");
  expect(fetcher.mock.calls.map((call) => call[1]?.method ?? "GET")).toEqual([
    "PATCH",
    "GET",
  ]);
});

it.each([
  ["note", 1000],
  ["next_action", 500],
] as const)(
  "enforces %s Unicode limit and keeps relevant section open",
  async (field, limit) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(fresh({ [field]: "😀".repeat(limit) })),
    );
    setup();
    fireEvent.click(toggle(field));
    edit(field, "x".repeat(limit + 1));
    fireEvent.click(save(field));
    expect(section(field).getByRole("alert")).toHaveTextContent(`до ${limit}`);
    expect(fetch).not.toHaveBeenCalled();
    edit(field, "😀".repeat(limit));
    fireEvent.click(save(field));
    await waitFor(() =>
      expect(toggle(field)).toHaveAttribute("aria-expanded", "false"),
    );
  },
);

it("keeps existing date visible after action text save", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(fresh({ next_action: "Prepare" })),
  );
  setup();
  fireEvent.click(toggle("next_action"));
  edit("next_action", "Prepare");
  fireEvent.click(save("next_action"));
  await waitFor(() =>
    expect(toggle("next_action")).toHaveAttribute("aria-expanded", "false"),
  );
  expect(screen.getByText(/Существующая дата/)).toHaveTextContent("2026-10-01");
});

it("keeps dirty text through a status refresh and adopts fresh values when clean", () => {
  const { rerender } = setup();
  fireEvent.click(toggle("note"));
  edit("note", "Local draft");
  rerender(
    <ApplicationNotesEditor
      application={{ ...saved.application, status: "applied" }}
      onSaved={vi.fn()}
    />,
  );
  expect(input("note")).toHaveValue("Local draft");
  fireEvent.click(section("note").getByRole("button", { name: "Отмена" }));
  rerender(
    <ApplicationNotesEditor
      application={{ ...saved.application, note: "Fresh external note" }}
      onSaved={vi.fn()}
    />,
  );
  expect(
    screen.getByText("Fresh external note", {
      selector: "p.application-note-summary",
    }),
  ).toBeInTheDocument();
});

it("merges independent responses arriving in reverse order without losing the other field or status", async () => {
  let notes!: (response: Response) => void;
  let action!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(
      (_url, init) =>
        new Promise<Response>((resolve) => {
          if (JSON.parse(init.body).note) notes = resolve;
          else action = resolve;
        }),
    ),
  );
  render(<ApplicationStatusDetail detail={saved} />);
  fireEvent.click(toggle("note"));
  fireEvent.click(toggle("next_action"));
  edit("note", "New note");
  edit("next_action", "New action");
  fireEvent.click(save("note"));
  fireEvent.click(save("next_action"));
  action(fresh({ next_action: "New action" }));
  await waitFor(() =>
    expect(toggle("next_action")).toHaveAttribute("aria-expanded", "false"),
  );
  notes(fresh({ note: "New note" }));
  await waitFor(() =>
    expect(toggle("note")).toHaveAttribute("aria-expanded", "false"),
  );
  expect(
    screen.getByText("New note", { selector: "p.application-note-summary" }),
  ).toBeInTheDocument();
  expect(
    screen.getByText("New action", { selector: "p.application-note-summary" }),
  ).toBeInTheDocument();
  expect(document.querySelector(".status-badge")).toHaveTextContent(
    "Собеседование",
  );
});
