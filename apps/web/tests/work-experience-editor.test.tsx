import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { useState } from "react";
import { WorkExperienceList } from "../components/work-experience-editor";
import type { WorkExperienceEntry } from "../lib/profile";
const entry: WorkExperienceEntry = {
  id: 42,
  company: "Acme",
  position: "Engineer",
  engagement_kind: "employment",
  start_year: 2020,
  start_month: null,
  end_year: 2024,
  end_month: 1,
  is_current: false,
  duration_months: null,
};
function Workspace({ initial = [entry] }: { initial?: WorkExperienceEntry[] }) {
  const [items, setItems] = useState(initial);
  return <WorkExperienceList items={items} onChange={setItems} />;
}
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
function editor() {
  return screen.getByRole("form");
}
function edit() {
  fireEvent.click(screen.getByRole("button", { name: "Редактировать опыт" }));
}

it("empty state opens compact add editor, dirty save, cancel without mutation and focus return", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  render(<Workspace initial={[]} />);
  expect(screen.getByText("Опыт работы пока не добавлен.")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Добавить опыт" }));
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  expect(screen.getByLabelText("Должность")).toHaveFocus();
  fireEvent.change(screen.getByLabelText("Должность"), {
    target: { value: "Developer" },
  });
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(fetcher).not.toHaveBeenCalled();
  expect(screen.queryByRole("form")).toBeNull();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Добавить опыт" })).toHaveFocus(),
  );
});

it("creates from canonical response, refreshes card, preserves partial dates and localizes kind", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    Response.json({
      ...entry,
      id: 43,
      company: null,
      position: "Canonical",
      is_current: null,
      end_year: null,
      end_month: null,
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<Workspace initial={[]} />);
  fireEvent.click(screen.getByRole("button", { name: "Добавить опыт" }));
  fireEvent.change(screen.getByLabelText("Должность"), {
    target: { value: " Draft " },
  });
  fireEvent.change(screen.getByLabelText("Год начала"), {
    target: { value: "2020" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByText("Canonical");
  expect(screen.queryByRole("form")).toBeNull();
  expect(fetcher.mock.calls[0][1].method).toBe("POST");
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({
    position: " Draft ",
    start_year: 2020,
    start_month: null,
  });
  expect(screen.getByText(/Работа по найму/)).toBeVisible();
});

it("edits one card, sends only changed fields and returns focus after canonical save", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(Response.json({ ...entry, position: "Senior" }));
  vi.stubGlobal("fetch", fetcher);
  render(<Workspace />);
  edit();
  expect(screen.getByRole("button", { name: "Добавить опыт" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Должность"), {
    target: { value: "Senior" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByText("Senior");
  expect(fetcher.mock.calls[0][0]).toBe("/api/profile/work-experiences/42");
  expect(fetcher.mock.calls[0][1].method).toBe("PATCH");
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
    position: "Senior",
  });
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Редактировать опыт" }),
    ).toHaveFocus(),
  );
});

it("cancel edit discards draft without a request", () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  render(<Workspace />);
  edit();
  fireEvent.change(screen.getByLabelText("Компания / проект"), {
    target: { value: "Other" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(screen.getByText("Acme")).toBeVisible();
  expect(fetcher).not.toHaveBeenCalled();
});

it("current checkbox clears end parts and removes end date controls from the accessible form", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    Response.json({
      ...entry,
      is_current: true,
      end_year: null,
      end_month: null,
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<Workspace />);
  edit();
  fireEvent.click(
    screen.getByLabelText("По настоящее время", { selector: "input" }),
  );
  expect(screen.queryByLabelText("Год окончания")).toBeNull();
  expect(screen.queryByRole("group", { name: "Окончание" })).toBeNull();
  expect(screen.queryByLabelText("Статус периода")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByText(/Сейчас/);
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
    is_current: true,
    end_year: null,
    end_month: null,
  });
  edit();
  fireEvent.click(screen.getByRole("checkbox", { name: "По настоящее время" }));
  expect(screen.getByLabelText("Год окончания")).toBeEnabled();
  expect(screen.getByLabelText("Год окончания")).toHaveValue(null);
});

it("safe API validation retains draft and associates field errors", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json(
        {
          code: "work_invalid",
          fieldErrors: { start_month: "Проверьте месяц." },
        },
        { status: 422 },
      ),
    ),
  );
  render(<Workspace />);
  edit();
  fireEvent.change(screen.getByLabelText("Месяц начала"), {
    target: { value: "4" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Месяц начала")).toHaveValue(4);
  expect(screen.getByLabelText("Месяц начала")).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  expect(screen.getByLabelText("Месяц начала")).toHaveAccessibleDescription(
    "Проверьте месяц.",
  );
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeEnabled();
});

it("prevents double submit, disables save while pending, keeps cancel available", async () => {
  let resolve!: (value: Response) => void;
  const fetcher = vi.fn(
    () =>
      new Promise<Response>((r) => {
        resolve = r;
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<Workspace />);
  edit();
  fireEvent.change(screen.getByLabelText("Должность"), {
    target: { value: "New" },
  });
  fireEvent.submit(editor());
  fireEvent.submit(editor());
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Сохраняем…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Отмена" })).toBeEnabled();
  resolve(Response.json({ ...entry, position: "New" }));
  await screen.findByText("New");
});

it("delete requires inline confirmation, cancel is read only, failure retains confirmation, success removes card", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({ code: "work_conflict" }, { status: 409 }),
    )
    .mockResolvedValueOnce(Response.json({ ok: true }));
  vi.stubGlobal("fetch", fetcher);
  render(<Workspace />);
  fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
  expect(screen.getByText("Удалить этот опыт работы?")).toBeVisible();
  expect(screen.getByRole("button", { name: "Удалить" })).toHaveClass(
    "work-text-action",
    "work-delete",
  );
  expect(screen.getByRole("button", { name: "Отмена" })).toHaveClass(
    "work-text-action",
  );
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
  fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
  await screen.findByRole("alert");
  expect(screen.getByText("Acme")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
  await screen.findByText("Опыт работы пока не добавлен.");
  expect(fetcher.mock.calls[0][1].method).toBe("DELETE");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Добавить опыт" })).toHaveFocus(),
  );
});

it("one editor at a time and date controls use labeled fieldsets with wrapping action groups", () => {
  render(
    <Workspace initial={[entry, { ...entry, id: 43, position: "Other" }]} />,
  );
  fireEvent.click(
    screen.getAllByRole("button", { name: "Редактировать опыт" })[0],
  );
  expect(screen.getAllByRole("form")).toHaveLength(1);
  expect(
    screen.getByRole("button", { name: "Редактировать опыт" }),
  ).toBeDisabled();
  expect(
    within(screen.getByRole("group", { name: "Начало" })).getByLabelText(
      "Год начала",
    ),
  ).toBeVisible();
  expect(editor().querySelectorAll(".work-date-controls")).toHaveLength(2);
  expect(editor().querySelector(".work-actions")).not.toBeNull();
});

it("checks uncertain PATCH before retry and preserves concurrent unrelated fields", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({ code: "work_unconfirmed" }, { status: 503 }),
    )
    .mockResolvedValueOnce(
      Response.json({ items: [{ ...entry, company: "Concurrent company" }] }),
    )
    .mockResolvedValueOnce(
      Response.json({
        ...entry,
        company: "Concurrent company",
        position: "Senior",
      }),
    );
  vi.stubGlobal("fetch", fetcher);
  render(<Workspace />);
  edit();
  fireEvent.change(screen.getByLabelText("Должность"), {
    target: { value: "Senior" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Должность")).toHaveValue("Senior");
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  fireEvent.click(
    screen.getByRole("button", { name: "Проверить актуальный опыт" }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Компания / проект")).toHaveValue(
      "Concurrent company",
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByText("Senior");
  expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({
    position: "Senior",
  });
});

it.each([
  ["  Review\u00a0\u00a0Studio  ", "Review Studio"],
  [" \u2003 ", null],
])(
  "uncertain create recognizes canonical text %j without another POST",
  async (company, canonicalCompany) => {
    const created = {
      ...entry,
      company: canonicalCompany,
      position: "Senior Engineer",
      engagement_kind: "freelance",
      start_year: 2020,
      end_year: null,
      end_month: null,
      is_current: null,
    };
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new Error("lost response"))
      .mockResolvedValueOnce(Response.json({ items: [created] }));
    vi.stubGlobal("fetch", fetcher);
    render(<Workspace initial={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Добавить опыт" }));
    fireEvent.change(screen.getByLabelText("Должность"), {
      target: { value: "  Senior   Engineer  " },
    });
    fireEvent.change(screen.getByLabelText("Компания / проект"), {
      target: { value: company },
    });
    fireEvent.change(screen.getByLabelText("Год начала"), {
      target: { value: "2020" },
    });
    fireEvent.click(screen.getByRole("combobox", { name: /Тип занятости/ }));
    fireEvent.click(screen.getByRole("option", { name: "Фриланс" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByRole("alert");
    fireEvent.click(
      screen.getByRole("button", { name: "Проверить актуальный опыт" }),
    );
    await screen.findByText("Senior Engineer");
    expect(screen.queryByRole("form")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Опыт работы сохранён.",
    );
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[0][1].method).toBe("POST");
    expect(fetcher.mock.calls[1][1]?.method).toBeUndefined();
  },
);

it.each([
  [" Review   Studio ", "Review Studio"],
  [" \u00a0 ", null],
])(
  "uncertain PATCH recognizes canonical text %j and preserves unrelated server fields",
  async (company, canonicalCompany) => {
    const original = {
      ...entry,
      is_current: null,
      end_year: null,
      end_month: null,
    };
    const persisted = {
      ...original,
      company: canonicalCompany,
      position: "Senior Engineer",
      engagement_kind: "freelance" as const,
    };
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new Error("lost response"))
      .mockResolvedValueOnce(Response.json({ items: [persisted] }));
    vi.stubGlobal("fetch", fetcher);
    render(<Workspace initial={[original]} />);
    edit();
    fireEvent.change(screen.getByLabelText("Должность"), {
      target: { value: " Senior   Engineer " },
    });
    fireEvent.change(screen.getByLabelText("Компания / проект"), {
      target: { value: company },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByRole("alert");
    fireEvent.click(
      screen.getByRole("button", { name: "Проверить актуальный опыт" }),
    );
    await screen.findByText("Senior Engineer");
    expect(screen.queryByRole("form")).toBeNull();
    expect(screen.getByText(/Фриланс/)).toBeVisible();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[0][1].method).toBe("PATCH");
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
      company,
      position: " Senior   Engineer ",
    });
    expect(fetcher.mock.calls[1][1]?.method).toBeUndefined();
  },
);

it.each([
  { position: "senior Engineer" },
  { position: "Senior-Engineer" },
  { position: "\ufeffSenior Engineer" },
  { company: "Other" },
  { engagement_kind: "employment" },
  { start_year: 2021 },
  { start_month: 1 },
  { end_year: 2024, is_current: false },
  { end_year: 2024, end_month: 1, is_current: false },
  { is_current: false },
])(
  "uncertain create does not conflate distinct canonical fields %j",
  async (difference) => {
    const candidate = {
      ...entry,
      company: null,
      position: "Senior Engineer",
      engagement_kind: "unknown",
      start_year: 2020,
      start_month: null,
      end_year: null,
      end_month: null,
      is_current: null,
      ...difference,
    };
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new Error("lost response"))
      .mockResolvedValueOnce(Response.json({ items: [candidate] }));
    vi.stubGlobal("fetch", fetcher);
    render(<Workspace initial={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Добавить опыт" }));
    fireEvent.change(screen.getByLabelText("Должность"), {
      target: { value: " Senior   Engineer " },
    });
    fireEvent.change(screen.getByLabelText("Год начала"), {
      target: { value: "2020" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByRole("alert");
    fireEvent.click(
      screen.getByRole("button", { name: "Проверить актуальный опыт" }),
    );
    await screen.findByText(/Актуальные данные проверены/);
    expect(editor()).toBeVisible();
    expect(screen.queryByRole("status")).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(2);
  },
);

it("Cancel while submitting waits for the sent request and reflects its canonical result", async () => {
  let resolve!: (response: Response) => void;
  const fetcher = vi.fn(
    () =>
      new Promise<Response>((r) => {
        resolve = r;
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<Workspace />);
  edit();
  fireEvent.change(screen.getByLabelText("Должность"), {
    target: { value: "Sent" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Запрос уже отправлен");
  resolve(Response.json({ ...entry, position: "Sent" }));
  await screen.findByText("Sent");
  expect(screen.queryByRole("form")).toBeNull();
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("a sent save updates the parent collection after the experience editor is hidden", async () => {
  let resolve!: (response: Response) => void;
  const fetcher = vi.fn(
    () =>
      new Promise<Response>((r) => {
        resolve = r;
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  function Parent() {
    const [items, setItems] = useState([entry]);
    const [visible, setVisible] = useState(true);
    return (
      <>
        <button onClick={() => setVisible(!visible)}>Toggle section</button>
        <button
          onClick={() =>
            setItems((current) => [
              ...current,
              { ...entry, id: 43, position: "Concurrent entry" },
            ])
          }
        >
          Add elsewhere
        </button>
        {visible && <WorkExperienceList items={items} onChange={setItems} />}
      </>
    );
  }
  render(<Parent />);
  edit();
  fireEvent.change(screen.getByLabelText("Должность"), {
    target: { value: "Persisted" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  fireEvent.click(screen.getByRole("button", { name: "Toggle section" }));
  fireEvent.click(screen.getByRole("button", { name: "Add elsewhere" }));
  resolve(Response.json({ ...entry, position: "Persisted" }));
  await waitFor(() => expect(fetcher).toHaveResolved());
  fireEvent.click(screen.getByRole("button", { name: "Toggle section" }));
  expect(await screen.findByText("Persisted")).toBeVisible();
  expect(screen.getByText("Concurrent entry")).toBeVisible();
});

it("preserves unknown period status when only the position changes", async () => {
  const unknown = {
    ...entry,
    is_current: null,
    end_year: null,
    end_month: null,
  };
  const fetcher = vi
    .fn()
    .mockResolvedValue(Response.json({ ...unknown, position: "Updated" }));
  vi.stubGlobal("fetch", fetcher);
  render(<Workspace initial={[unknown]} />);
  edit();
  expect(screen.queryByLabelText("Статус периода")).toBeNull();
  expect(screen.getByLabelText("Год окончания")).toBeEnabled();
  fireEvent.change(screen.getByLabelText("Должность"), {
    target: { value: "Updated" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByText("Updated");
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
    position: "Updated",
  });
});

it("entering an end date explicitly marks the period as ended", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(entry));
  vi.stubGlobal("fetch", fetcher);
  render(<Workspace initial={[]} />);
  fireEvent.click(screen.getByRole("button", { name: "Добавить опыт" }));
  fireEvent.change(screen.getByLabelText("Должность"), {
    target: { value: "Engineer" },
  });
  fireEvent.change(screen.getByLabelText("Год окончания"), {
    target: { value: "2024" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByText("Engineer");
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({
    end_year: 2024,
    end_month: null,
    is_current: false,
  });
});

it("exposes editor layout state and restores read layout after cancel and save", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ ...entry, position: "Saved" })),
  );
  const { container } = render(<Workspace />);
  const list = container.querySelector(".work-experience-list")!;
  expect(list).toHaveAttribute("data-editing", "false");
  fireEvent.click(screen.getByRole("button", { name: "Добавить опыт" }));
  expect(list).toHaveAttribute("data-editing", "true");
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(list).toHaveAttribute("data-editing", "false");
  edit();
  expect(list).toHaveAttribute("data-editing", "true");
  fireEvent.change(screen.getByLabelText("Должность"), {
    target: { value: "Saved" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByText("Saved");
  expect(list).toHaveAttribute("data-editing", "false");
});

it("announces success for four seconds, resets the timer and clears it on unmount", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  const fetcher = vi.fn().mockImplementation(async () =>
    Response.json({
      ...entry,
      position: fetcher.mock.calls.length === 1 ? "First" : "Second",
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  const view = render(<Workspace />);
  async function save(position: string) {
    edit();
    fireEvent.change(screen.getByLabelText("Должность"), {
      target: { value: position },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    });
  }
  await save("First");
  expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
  act(() => vi.advanceTimersByTime(3000));
  expect(screen.getByRole("status")).toHaveTextContent("Опыт работы сохранён.");
  await save("Second");
  act(() => vi.advanceTimersByTime(3999));
  expect(screen.getByRole("status")).toBeVisible();
  act(() => vi.advanceTimersByTime(1));
  expect(screen.queryByRole("status")).toBeNull();
  const clear = vi.spyOn(globalThis, "clearTimeout");
  const schedule = vi.spyOn(globalThis, "setTimeout");
  await save("Third");
  const statusTimer =
    schedule.mock.results[
      schedule.mock.calls.findIndex((call) => call[1] === 4000)
    ].value;
  view.unmount();
  expect(clear).toHaveBeenCalledWith(statusTimer);
  clear.mockRestore();
  schedule.mockRestore();
});

it("engagement custom select sends the canonical enum without changing other fields", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(
      Response.json({ ...entry, engagement_kind: "internship" }),
    );
  vi.stubGlobal("fetch", fetcher);
  render(<Workspace />);
  edit();
  fireEvent.click(screen.getByRole("combobox", { name: /^Тип занятости / }));
  fireEvent.click(screen.getByRole("option", { name: "Стажировка" }));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByText(/Стажировка/);
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
    engagement_kind: "internship",
  });
});
